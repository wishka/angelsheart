#!/usr/bin/env bash
#
# Однократная настройка репозитория на GitHub под CI/CD:
#   • ветка develop (от main, если её ещё нет);
#   • окружения staging и production — production с подтверждением
#     выкладки и только из main / тегов v*;
#   • защита веток main и develop: слияние только через pull request
#     с зелёными проверками, без force-push и удаления.
#
# Нужен GitHub CLI под учётной записью с правами администратора:
#   gh auth login
#   bash scripts/setup-github.sh
#
# Скрипт можно запускать повторно — он приводит настройки к описанным.
# Секреты он НЕ задаёт: их значения есть только у вас (docs/CICD.md).

set -Eeuo pipefail

REPO="${REPO:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}"
ME_ID="$(gh api user -q .id)"
ME="$(gh api user -q .login)"
CHECKS='["server (sqlite)", "server (postgres)", "android (debug)"]'

echo "Репозиторий: $REPO, администратор: $ME"

# ---------- Ветка develop ----------
if gh api "repos/$REPO/branches/develop" >/dev/null 2>&1; then
    echo "✓ develop уже есть"
else
    MAIN_SHA="$(gh api "repos/$REPO/git/ref/heads/main" -q .object.sha)"
    gh api "repos/$REPO/git/refs" -f ref=refs/heads/develop -f sha="$MAIN_SHA" >/dev/null
    echo "✓ создана develop от main ($MAIN_SHA)"
fi

# ---------- Окружения ----------
env_policy() {  # env, затем пары «тип:шаблон»
    local env="$1"; shift
    for rule in "$@"; do
        gh api -X POST "repos/$REPO/environments/$env/deployment-branch-policies" \
            -f name="${rule#*:}" -f type="${rule%%:*}" >/dev/null 2>&1 || true
    done
}

gh api -X PUT "repos/$REPO/environments/staging" --input - >/dev/null <<JSON
{"deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}}
JSON
# main — для ручного отката staging (workflow Rollback запускается с main)
env_policy staging branch:develop branch:main
echo "✓ окружение staging: из develop (и main — для отката)"

gh api -X PUT "repos/$REPO/environments/production" --input - >/dev/null <<JSON
{
  "reviewers": [{"type": "User", "id": $ME_ID}],
  "deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}
}
JSON
env_policy production branch:main tag:v*
echo "✓ окружение production: из main и тегов v*, с подтверждением ($ME)"

# ---------- Защита веток ----------
protect() {
    local branch="$1" reviews="$2"
    gh api -X PUT "repos/$REPO/branches/$branch/protection" --input - >/dev/null <<JSON
{
  "required_status_checks": {"strict": true, "contexts": $CHECKS},
  "enforce_admins": false,
  "required_pull_request_reviews": {
    "required_approving_review_count": $reviews,
    "dismiss_stale_reviews": true
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_conversation_resolution": true
}
JSON
    echo "✓ $branch: только PR, обязательные проверки, без force-push"
}
# Пока разработчик один, обязательных одобрений 0: одобрить собственный
# PR GitHub не даёт. Появится второй — поднимите до 1 (REVIEWS=1).
protect main "${REVIEWS:-0}"
protect develop 0

cat <<'TEXT'

Осталось задать секреты и переменные (Settings → Environments →
staging / production). Список и откуда брать значения — docs/CICD.md,
раздел «Секреты и переменные». Например:

  gh secret set DEPLOY_SSH_KEY      --env production < angelsheart-deploy
  gh secret set DEPLOY_KNOWN_HOSTS  --env production --body "$(ssh-keyscan -H example.ru)"
  gh variable set DEPLOY_HOST       --env production --body example.ru
  gh variable set PUBLIC_URL        --env production --body https://angel-helper.ru
TEXT
