{% autoescape off %}{% comment %}
    Это JavaScript, а не HTML: автоэкранирование превратило бы апостроф
    или & в пути статики в &#x27; / &amp; и молча сломало бы service
    worker. Многострочный {# #} здесь не годится — он однострочный,
    и хвост комментария уехал бы прямо в код.
{% endcomment %}
/*
 * Service worker сервиса «Ангел-Хранитель».
 *
 * Главное правило: здесь не кэшируется ничего, что связано с деньгами
 * и персональными данными. Кэш браузера переживает выход из учётной
 * записи и доступен любому, кто взял телефон в руки, — страница с
 * балансом или паспортными данными в нём означала бы, что мы аккуратно
 * зашифровали данные на сервере и положили их копию на устройство.
 *
 * Поэтому кэшируются только оформление (стили, шрифты, значки) и
 * страница «нет сети». Всё остальное идёт в сеть, и если сети нет —
 * человек видит честное сообщение, а не вчерашний баланс.
 *
 * Файл собирается вью main.pwa.service_worker: пути к статике меняются
 * при каждом collectstatic.
 */

const VERSION = '{{ version }}';
const SHELL_CACHE = `angelsheart-shell-${VERSION}`;
const OFFLINE_URL = '{{ offline_url }}';

const PRECACHE = [
    OFFLINE_URL,
    {% for url in precache %}'{{ url }}',
    {% endfor %}
];

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(SHELL_CACHE)
            // Отдельными запросами, а не addAll: addAll срывается целиком,
            // если хоть один файл не отдался, и тогда приложение остаётся
            // вообще без офлайн-страницы
            .then((cache) => Promise.all(
                // credentials: 'omit' — обязательно. По умолчанию запрос
                // из cache.add() уходит с кукой сессии, и сервер отдаёт
                // страницу, собранную для текущего пользователя. Для
                // офлайн-страницы это означало бы её копию с чужим
                // балансом в кэше, который переживает выход из учётной
                // записи. Вторая линия защиты — сама офлайн-страница
                // ничего не знает о пользователе (см. её комментарий).
                PRECACHE.map((url) => fetch(url, { credentials: 'omit', cache: 'reload' })
                    .then((response) => (response.ok ? cache.put(url, response) : null))
                    .catch(() => null))
            ))
            .then(() => self.skipWaiting())
    );
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys()
            .then((names) => Promise.all(
                names
                    .filter((name) => name.startsWith('angelsheart-') && name !== SHELL_CACHE)
                    .map((name) => caches.delete(name))
            ))
            .then(() => self.clients.claim())
    );
});

/* Адреса, которые не должны попадать в кэш ни при каких условиях. */
function isSensitive(url) {
    return (
        url.pathname.startsWith('/admin/')
        || url.pathname.startsWith('/api/')
        || url.pathname.startsWith('/verification/')
        || url.pathname.startsWith('/fundraise-document/')
        || url.pathname.startsWith('/my-consents/')
        || url.pathname.startsWith('/payment/')
        || url.pathname.startsWith('/media/')
        // Страницы с деньгами и личными данными. Навигации и так не
        // кэшируются, так что сегодня этот список ни на что не влияет,
        // — он стоит здесь на случай, когда кто-нибудь решит «а вот эту
        // страницу можно бы и кэшировать». Пусть тогда перечень уже
        // существует, а не пишется заново по памяти.
        || url.pathname.startsWith('/history/')
        || url.pathname.startsWith('/transfer/')
        || url.pathname.startsWith('/topup/')
        || url.pathname.startsWith('/withdrawal/')
        || url.pathname.startsWith('/my-')
        || url.pathname.startsWith('/profile/')
        || url.pathname.startsWith('/2fa/')
        || url.pathname.startsWith('/restrictions/')
        || url.pathname.startsWith('/moderation/')
    );
}

/*
 * Запасной ответ, когда офлайн-страница не закэшировалась.
 *
 * respondWith(undefined) — это исключение внутри service worker, и
 * человек вместо страницы видит стандартную ошибку браузера. Лучше
 * несколько строк разметки прямо здесь, чем «страница недоступна».
 */
function offlineFallback() {
    return new Response(
        '<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">'
        + '<meta name="viewport" content="width=device-width, initial-scale=1">'
        + '<title>Нет соединения</title></head>'
        + '<body style="font-family: sans-serif; text-align: center; padding: 3rem 1.5rem;">'
        + '<h1 style="color:#d4737a">Нет соединения</h1>'
        + '<p>Проверьте подключение и обновите страницу.</p></body></html>',
        { status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8' } }
    );
}

function isStatic(url) {
    return url.pathname.startsWith('/static/');
}

self.addEventListener('fetch', (event) => {
    const request = event.request;

    // Всё, кроме GET, уходит в сеть без участия кэша: перевод денег,
    // отправленный из кэша, — это перевод, которого не было
    if (request.method !== 'GET') {
        return;
    }

    const url = new URL(request.url);

    // Чужие адреса не трогаем вовсе — их у сайта и не должно быть
    if (url.origin !== self.location.origin) {
        return;
    }

    // Страницы: всегда сеть. Без неё — честное сообщение вместо
    // устаревших цифр, которые человек примет за настоящие.
    //
    // Проверка на «денежный» адрес здесь НЕ делается намеренно: ни одна
    // навигация в кэш не попадает, а вот отдать офлайн-страницу нужно
    // как раз на них. Раньше этот обработчик пропускал такие адреса
    // мимо себя, и человек, потерявший сеть на «Истории», получал не
    // страницу «нет соединения», а серую ошибку браузера — то есть
    // офлайн-режим не работал ровно там, где он и нужен.
    if (request.mode === 'navigate') {
        event.respondWith(
            fetch(request).catch(
                () => caches.match(OFFLINE_URL).then((cached) => cached || offlineFallback())
            )
        );
        return;
    }

    // Оформление: сначала кэш, параллельно обновление в фоне.
    // Шрифты и значки не меняются, а грузятся на каждой странице.
    // isSensitive стережёт именно запись в кэш: под /static/ денежных
    // адресов нет, но проверка стоит на случай, когда кто-нибудь решит
    // кэшировать что-то ещё.
    if (isStatic(url) && !isSensitive(url)) {
        event.respondWith(
            caches.match(request).then((cached) => {
                const network = fetch(request)
                    .then((response) => {
                        if (response.ok) {
                            const copy = response.clone();
                            caches.open(SHELL_CACHE).then((cache) => cache.put(request, copy));
                        }
                        return response;
                    })
                    .catch(() => cached || Response.error());
                return cached || network;
            })
        );
        return;
    }
});
{% endautoescape %}
