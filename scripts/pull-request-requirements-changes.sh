GH_ORG="emmettbutler"
REPOSITORY="$GH_ORG/dd-trace-py"
BASE_BRANCH="feat/requirements-locking"
BRANCH_NAME_PREFIX="ci-reqs-bot/reqs-update-"
PR_NAME_PREFIX="[dnm] chore(tests): bot recompile requirements files for commit "
HEAD_HASH="$(git rev-parse "$BASE_BRANCH")"
BRANCH_NAME="${BRANCH_NAME_PREFIX}${HEAD_HASH}"
PR_NAME="${PR_NAME_PREFIX}${HEAD_HASH}"

dry_run=${1:-1}

git checkout -B "$BRANCH_NAME"
git add -A
git commit -m "recompile riot requirements"
git pull --rebase origin "$BRANCH_NAME"
git push emmettbutler "$BRANCH_NAME"

found_prs="$(gh search prs is:open base:"$BASE_BRANCH" "$PR_NAME_PREFIX" in:title --json state,id,title --repo $REPOSITORY)"
found_pr="$(echo "${found_prs}" | jq -r '.[0].id')"
if [[ -z "$found_pr" || "$found_pr" = "null" ]]
then
    cmd="gh pr create --base \"$BASE_BRANCH\" --title \"$PR_NAME\" --body \"This is just a test\" --repo \"$REPOSITORY\""
    echo "$cmd"
    if [[ "$dry_run" = "0" ]]
    then
        eval "$cmd"
    fi
fi

git checkout $BASE_BRANCH
