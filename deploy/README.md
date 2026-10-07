# Сервер: подготовка к автоматической выкладке

Делается один раз на каждом сервере (тестовом и боевом). Дальше код
выкладывает GitHub Actions: `develop` → тестовый, `main` → боевой.
Как устроен процесс целиком — в `docs/CICD.md`.

## 1. Пользователь и каталоги

```bash
sudo adduser --disabled-password --gecos "" deploy
sudo usermod -aG www-data deploy
sudo mkdir -p /srv/angelsheart/{releases,shared,venvs,backups}
sudo mkdir -p /srv/angelsheart/shared/{media,private-media,logs,app-release}
sudo chown -R deploy:www-data /srv/angelsheart
sudo apt install -y python3-venv rsync curl postgresql-client
```

Тестовый сервер на той же машине — в `/srv/angelsheart-staging`,
со своим `.env`, своей базой и портом 8001.

## 2. Настройки

`/srv/angelsheart/shared/.env` — по образцу `.env.example`, с боевыми
значениями (`DEBUG=False`, `DJANGO_SECRET_KEY`, `POSTGRES_*`,
`REDIS_URL`, `ALLOWED_HOSTS`, `BEHIND_PROXY=True` и т. д.).

```bash
sudo chown deploy:www-data /srv/angelsheart/shared/.env
sudo chmod 640 /srv/angelsheart/shared/.env
```

## 3. Gunicorn как сервис

```bash
sudo cp deploy/angelsheart.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable angelsheart
```

Запустится он после первой выкладки, когда появится `current`.

## 4. Право на перезапуск — и только на него

```bash
sudo visudo -f /etc/sudoers.d/angelsheart-deploy   # содержимое: deploy/sudoers.example
```

## 5. nginx

Фрагмент — `deploy/nginx.conf.example`. Главное: `private-media` наружу
не отдаётся.

## 6. Ключ для GitHub Actions

На своей машине:

```bash
ssh-keygen -t ed25519 -f angelsheart-deploy -C "github-actions" -N ""
ssh-copy-id -i angelsheart-deploy.pub deploy@<сервер>
ssh-keyscan -H <сервер>          # → секрет DEPLOY_KNOWN_HOSTS
```

Закрытый ключ `angelsheart-deploy` → секрет `DEPLOY_SSH_KEY` окружения
в GitHub (Settings → Environments → staging / production). Полный
список секретов — в `docs/CICD.md`.

## Что делать руками, если что-то пошло не так

```bash
ls -1t /srv/angelsheart/releases                 # какие релизы есть
readlink /srv/angelsheart/current                # какой работает
bash /srv/angelsheart/current/deploy/rollback.sh # вернуть предыдущий
journalctl -u angelsheart -n 100                 # журнал Gunicorn
curl -s http://127.0.0.1:8000/health/            # жив ли и какой версии
```
