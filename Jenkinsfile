// CI/CD for the project management app.
//   CI (every branch): backend tests, frontend tests, Docker image build.
//   CD (main only):    after manual approval, update the server over SSH as described in deploy.md.
//
// Jenkins agent: Linux with Docker (the jenkins user must be able to run `docker`), plus git and ssh.
// Plugins: Pipeline, Git, JUnit, Credentials Binding.
// Deployment is skipped until DEPLOY_HOST is set. See "CI/CD with Jenkins" in deploy.md.

pipeline {
    agent any

    options {
        disableConcurrentBuilds()
        buildDiscarder(logRotator(numToKeepStr: '20'))
        timeout(time: 90, unit: 'MINUTES')
    }

    environment {
        // unique per build, used to name the throwaway containers, network and images
        CI_ID = "${env.BUILD_TAG.toLowerCase().replaceAll('[^a-z0-9_.-]', '-')}"
        DEPLOY_BRANCH = 'main'
    }

    stages {
        stage('Prepare') {
            steps {
                sh 'rm -rf reports && mkdir reports'
            }
        }

        stage('Test') {
            parallel {
                stage('Backend') {
                    steps {
                        // The tests need a MongoDB replica set (for transactions). Code is copied in with `docker cp`
                        // rather than mounted, so this also works when Jenkins itself runs in a container.
                        sh '''
                            docker network create "$CI_ID"
                            docker run -d --name "$CI_ID-mongo" --network "$CI_ID" --network-alias mongo mongo:7 --replSet rs0

                            wait_for() {
                                for i in $(seq 1 60); do
                                    docker exec "$CI_ID-mongo" mongosh --quiet --eval "$1" >/dev/null 2>&1 && return 0
                                    sleep 2
                                done
                                echo "MongoDB did not become ready"; docker logs --tail 50 "$CI_ID-mongo"; return 1
                            }
                            wait_for 'db.adminCommand({ping: 1})'
                            docker exec "$CI_ID-mongo" mongosh --quiet --eval 'rs.initiate({_id: "rs0", members: [{_id: 0, host: "mongo:27017"}]})'
                            wait_for 'if (!db.hello().isWritablePrimary) quit(1)'

                            docker create --name "$CI_ID-backend" --network "$CI_ID" -w /app \
                                -e TEST_MONGODB_URI="mongodb://mongo:27017/?directConnection=true" \
                                python:3.12-slim sh -c "pip install --quiet --no-cache-dir -r requirements.txt && python -m pytest -q -p no:cacheprovider --junitxml=/tmp/backend.xml"
                            docker cp backend/. "$CI_ID-backend:/app"

                            status=0
                            docker start -a "$CI_ID-backend" || status=$?
                            docker cp "$CI_ID-backend:/tmp/backend.xml" reports/ || true
                            exit $status
                        '''
                    }
                }

                stage('Frontend') {
                    steps {
                        sh '''
                            docker create --name "$CI_ID-frontend" -w /app node:22-alpine \
                                sh -c "npm ci --no-audit --no-fund && npx vitest run --reporter=default --reporter=junit --outputFile.junit=/tmp/frontend.xml"
                            docker cp frontend/. "$CI_ID-frontend:/app"

                            status=0
                            docker start -a "$CI_ID-frontend" || status=$?
                            docker cp "$CI_ID-frontend:/tmp/frontend.xml" reports/ || true
                            exit $status
                        '''
                    }
                }
            }
        }

        stage('Build images') {
            steps {
                // Proves both Dockerfiles build (including the production frontend bundle) before anything is deployed.
                sh '''
                    docker build -t "$CI_ID-backend-image" backend
                    docker build -t "$CI_ID-frontend-image" frontend
                '''
            }
        }

        stage('Deploy') {
            when {
                beforeInput true
                allOf {
                    expression { (env.BRANCH_NAME ?: env.GIT_BRANCH ?: '').replaceFirst('^origin/', '') == env.DEPLOY_BRANCH }
                    expression { env.DEPLOY_HOST?.trim() }
                }
            }
            input {
                message 'Deploy this build to the server?'
                ok 'Deploy'
            }
            steps {
                withCredentials([sshUserPrivateKey(credentialsId: 'pms-deploy-ssh', keyFileVariable: 'SSH_KEY', usernameVariable: 'SSH_USER')]) {
                    // The server's .env and uploads volume stay where they are; only the code is moved to the tested commit.
                    sh '''
                        ssh -i "$SSH_KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes \
                            "$SSH_USER@$DEPLOY_HOST" "COMMIT='$GIT_COMMIT' DEPLOY_DIR='${DEPLOY_DIR:-ICTAKPMS-Mongo}' sh -s" <<'REMOTE'
set -e
cd "$DEPLOY_DIR"
previous=$(git rev-parse HEAD)
echo "Updating from $previous to $COMMIT"
git fetch --prune origin
git checkout --detach "$COMMIT"
docker compose up -d --build --remove-orphans

# /api/docs is answered by the backend through nginx, so this checks both containers
address=$(docker compose port frontend 80)
if ! curl -fsS -o /dev/null --retry 30 --retry-delay 5 --retry-all-errors "http://$address/api/docs"; then
    echo "The app did not respond. Previous commit was $previous (rollback steps are in deploy.md)."
    docker compose ps
    docker compose logs --tail 80 backend
    exit 1
fi
docker compose ps
docker image prune -f
REMOTE
                    '''
                }
            }
        }
    }

    post {
        always {
            junit testResults: 'reports/*.xml', allowEmptyResults: true
            sh '''
                docker rm -f -v "$CI_ID-backend" "$CI_ID-frontend" "$CI_ID-mongo" >/dev/null 2>&1 || true
                docker network rm "$CI_ID" >/dev/null 2>&1 || true
                docker rmi "$CI_ID-backend-image" "$CI_ID-frontend-image" >/dev/null 2>&1 || true
            '''
        }
    }
}
