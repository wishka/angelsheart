"""
Тесты сообщества: поиск людей, чаты, группы.

Каждый тест проверяет правило, нарушение которого было бы заметно
человеку или опасно для данных: чужой чат, анкета без согласия,
владелец, бросивший группу.
"""

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from main.models import UserConsent
from social import services
from social.models import (
    Chat, ChatMember, ChatMessage, Community, CommunityMembership, Interest, SocialProfile,
)


def make_user(username, **profile):
    user = User.objects.create_user(username=username, password='Sunrise-Harbor-42',
                                    email=f'{username}@example.com')
    interests = profile.pop('interests', [])
    discoverable = profile.pop('discoverable', False)
    social = SocialProfile.objects.create(user=user, is_discoverable=discoverable, **profile)
    if interests:
        social.interests.set(Interest.objects.filter(slug__in=interests))
    if discoverable:
        UserConsent.objects.create(user=user, consent_type='distribution',
                                   version=UserConsent.current_version(), is_accepted=True)
    return user


class SocialTestCase(TestCase):
    def setUp(self):
        cache.clear()  # счётчики ограничения запросов
        self.me = make_user('me')
        self.client = APIClient()
        self.client.force_authenticate(self.me)

    def results(self, response):
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()['results']


class PeopleSearchTests(SocialTestCase):
    def setUp(self):
        super().setUp()
        year = timezone.localdate().year
        self.anna = make_user('anna', display_name='Анна', city='Москва', gender='female',
                              birth_year=year - 25, interests=['photo', 'volunteering'],
                              discoverable=True)
        self.ivan = make_user('ivan', display_name='Иван', city='Казань', gender='male',
                              birth_year=year - 40, interests=['sport'], discoverable=True)
        self.hidden = make_user('hidden', city='Москва')  # показ в поиске не включён

    def search(self, **params):
        return [p['username'] for p in self.results(self.client.get('/api/people/', params))]

    def test_only_discoverable_people_are_found(self):
        self.assertEqual(self.search(), ['anna', 'ivan'])

    def test_revoked_consent_hides_profile_immediately(self):
        UserConsent.objects.get(user=self.anna).revoke('отзыв')
        self.assertEqual(self.search(), ['ivan'])

    def test_filters(self):
        self.assertEqual(self.search(q='анн'), ['anna'])
        self.assertEqual(self.search(city='казань'), ['ivan'])
        self.assertEqual(self.search(gender='female'), ['anna'])
        self.assertEqual(self.search(age_min=30), ['ivan'])
        self.assertEqual(self.search(age_max=30), ['anna'])
        self.assertEqual(self.search(interests='sport,photo'), ['anna', 'ivan'])
        self.assertEqual(self.search(interests='volunteering'), ['anna'])

    def test_profile_does_not_leak_email(self):
        data = self.client.get(f'/api/people/{self.anna.pk}/').json()
        self.assertEqual(data['display_name'], 'Анна')
        self.assertNotIn('email', data)

    def test_hidden_profile_is_404(self):
        self.assertEqual(self.client.get(f'/api/people/{self.hidden.pk}/').status_code, 404)

    def test_enabling_discoverability_records_consent(self):
        response = self.client.patch('/api/profile/', {
            'city': 'Сочи', 'is_discoverable': True, 'interests': ['music'],
        }, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()['distribution_consent'])
        self.assertTrue(UserConsent.has_active_consent(self.me, 'distribution'))
        other = APIClient()
        other.force_authenticate(self.anna)
        found = [p['username'] for p in other.get('/api/people/', {'city': 'Сочи'}).json()['results']]
        self.assertEqual(found, ['me'])

    def test_birth_year_is_validated(self):
        response = self.client.patch('/api/profile/', {'birth_year': timezone.localdate().year},
                                      format='json')
        self.assertEqual(response.status_code, 400)


class ChatTests(SocialTestCase):
    def setUp(self):
        super().setUp()
        self.anna = make_user('anna')
        self.ivan = make_user('ivan')

    def test_direct_chat_is_created_once(self):
        first = self.client.post('/api/chats/', {'usernames': ['anna']}, format='json')
        second = self.client.post('/api/chats/', {'usernames': ['@anna']}, format='json')
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()['id'], second.json()['id'])
        self.assertEqual(first.json()['title'], 'anna')
        self.assertEqual(Chat.objects.count(), 1)

    def test_cannot_write_to_self_or_unknown(self):
        self.assertEqual(self.client.post('/api/chats/', {'usernames': ['me']}, format='json').status_code, 400)
        response = self.client.post('/api/chats/', {'usernames': ['nobody']}, format='json')
        self.assertIn('nobody', response.json()['error'])

    def test_group_chat_requires_title(self):
        response = self.client.post('/api/chats/', {'usernames': ['anna', 'ivan']}, format='json')
        self.assertEqual(response.status_code, 400)
        response = self.client.post('/api/chats/', {'usernames': ['anna', 'ivan'], 'title': 'Друзья'},
                                    format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['members_count'], 3)

    def test_messages_unread_and_polling(self):
        chat_id = self.client.post('/api/chats/', {'usernames': ['anna']}, format='json').json()['id']
        sent = self.client.post(f'/api/chats/{chat_id}/messages/', {'text': '  Привет  '}, format='json')
        self.assertEqual(sent.status_code, 201)
        self.assertEqual(sent.json()['text'], 'Привет')

        anna = APIClient()
        anna.force_authenticate(self.anna)
        chats = anna.get('/api/chats/').json()['results']
        self.assertEqual(chats[0]['unread_count'], 1)
        self.assertEqual(chats[0]['last_message']['text'], 'Привет')

        thread = anna.get(f'/api/chats/{chat_id}/messages/').json()
        self.assertEqual([m['text'] for m in thread['results']], ['Привет'])
        self.assertFalse(thread['results'][0]['is_mine'])
        self.assertEqual(anna.get('/api/chats/').json()['results'][0]['unread_count'], 0)

        last_id = thread['results'][-1]['id']
        anna.post(f'/api/chats/{chat_id}/messages/', {'text': 'Ответ'}, format='json')
        fresh = self.client.get(f'/api/chats/{chat_id}/messages/', {'after': last_id}).json()
        self.assertEqual([m['text'] for m in fresh['results']], ['Ответ'])

    def test_outsider_cannot_read_or_write(self):
        chat_id = self.client.post('/api/chats/', {'usernames': ['anna']}, format='json').json()['id']
        ivan = APIClient()
        ivan.force_authenticate(self.ivan)
        self.assertEqual(ivan.get(f'/api/chats/{chat_id}/messages/').status_code, 404)
        self.assertEqual(ivan.post(f'/api/chats/{chat_id}/messages/', {'text': 'x'},
                                   format='json').status_code, 404)
        self.assertEqual(ivan.get('/api/chats/').json()['results'], [])

    def test_empty_message_rejected(self):
        chat_id = self.client.post('/api/chats/', {'usernames': ['anna']}, format='json').json()['id']
        response = self.client.post(f'/api/chats/{chat_id}/messages/', {'text': '   '}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(ChatMessage.objects.count(), 0)

    def test_leave_group_chat_but_not_direct(self):
        direct = self.client.post('/api/chats/', {'usernames': ['anna']}, format='json').json()['id']
        self.assertEqual(self.client.post(f'/api/chats/{direct}/leave/').status_code, 400)
        group = self.client.post('/api/chats/', {'usernames': ['anna', 'ivan'], 'title': 'Т'},
                                 format='json').json()['id']
        self.assertEqual(self.client.post(f'/api/chats/{group}/leave/').status_code, 200)
        self.assertFalse(ChatMember.objects.filter(chat_id=group, user=self.me).exists())


class CommunityTests(SocialTestCase):
    def setUp(self):
        super().setUp()
        self.anna = make_user('anna')
        self.anna_client = APIClient()
        self.anna_client.force_authenticate(self.anna)

    def create(self, **data):
        payload = {'name': 'Волонтёры', 'topic': 'volunteering', **data}
        response = self.client.post('/api/groups/', payload, format='json')
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def test_create_open_group_and_join(self):
        group = self.create()
        self.assertEqual(group['my_status'], 'owner')
        self.assertEqual(group['topic']['title'], 'Волонтёрство')
        self.assertIsNotNone(group['chat_id'])

        # До вступления — ни участников, ни обсуждения
        before = self.anna_client.get(f"/api/groups/{group['id']}/").json()
        self.assertEqual(before['members'], [])
        self.assertIsNone(before['chat_id'])

        joined = self.anna_client.post(f"/api/groups/{group['id']}/join/").json()
        self.assertEqual(joined['my_status'], 'member')
        self.assertEqual(joined['members_count'], 2)
        # Обсуждение группы появилось в списке чатов
        chats = self.anna_client.get('/api/chats/').json()['results']
        self.assertEqual(chats[0]['community_id'], group['id'])

        left = self.anna_client.post(f"/api/groups/{group['id']}/leave/").json()
        self.assertEqual(left['my_status'], 'none')
        self.assertEqual(self.anna_client.get('/api/chats/').json()['results'], [])

    def test_private_group_needs_approval(self):
        group = self.create(is_private=True)
        pending = self.anna_client.post(f"/api/groups/{group['id']}/join/").json()
        self.assertEqual(pending['my_status'], 'pending')
        self.assertIsNone(pending['chat_id'])

        # Участник без прав принять заявку не может
        response = self.anna_client.post(f"/api/groups/{group['id']}/approve/", {'user_id': self.anna.pk})
        self.assertEqual(response.status_code, 403)

        detail = self.client.get(f"/api/groups/{group['id']}/").json()
        self.assertEqual([r['username'] for r in detail['pending_requests']], ['anna'])
        approved = self.client.post(f"/api/groups/{group['id']}/approve/", {'user_id': self.anna.pk}).json()
        self.assertEqual(approved['members_count'], 2)
        self.assertEqual(self.anna_client.get(f"/api/groups/{group['id']}/").json()['my_status'], 'member')

    def test_owner_cannot_leave(self):
        group = self.create()
        self.assertEqual(self.client.post(f"/api/groups/{group['id']}/leave/").status_code, 400)

    def test_search_and_mine(self):
        self.create(name='Бег по утрам', topic='sport')
        self.create(name='Кошки', topic='animals')
        names = [g['name'] for g in self.results(self.anna_client.get('/api/groups/', {'search': 'бег'}))]
        self.assertEqual(names, ['Бег по утрам'])
        names = [g['name'] for g in self.results(self.anna_client.get('/api/groups/', {'topic': 'animals'}))]
        self.assertEqual(names, ['Кошки'])
        self.assertEqual(self.results(self.anna_client.get('/api/groups/', {'mine': '1'})), [])


class AccountErasureTests(SocialTestCase):
    def test_erasure_keeps_conversation_but_removes_data(self):
        anna = make_user('anna', city='Москва', discoverable=True)
        chat, _ = services.get_or_create_direct_chat(self.me, anna)
        services.send_message(chat, anna, 'Секрет')
        group = services.create_community(anna, 'Группа Анны')
        services.join_community(group, self.me)

        services.erase_user_social_data(anna)

        self.assertFalse(SocialProfile.objects.filter(user=anna).exists())
        thread = self.client.get(f'/api/chats/{chat.pk}/messages/').json()['results']
        self.assertEqual(thread[0]['text'], 'Сообщение удалено')
        self.assertEqual(thread[0]['sender_name'], 'Удалённый пользователь')
        group.refresh_from_db()
        self.assertEqual(group.owner, self.me)
        self.assertEqual(
            CommunityMembership.objects.get(community=group, user=self.me).role, 'owner',
        )

    def test_lonely_group_is_deleted(self):
        anna = make_user('anna')
        services.create_community(anna, 'Пусто')
        services.erase_user_social_data(anna)
        self.assertFalse(Community.objects.exists())
        self.assertFalse(Chat.objects.exists())


class ProfilePutTests(SocialTestCase):
    def test_put_is_partial_update(self):
        self.client.put('/api/profile/', {'city': 'Тверь', 'about': 'Привет'}, format='json')
        response = self.client.put('/api/profile/', {'about': 'Новое'}, format='json')
        self.assertEqual(response.json()['city'], 'Тверь')
        self.assertEqual(response.json()['about'], 'Новое')


class BlockTests(SocialTestCase):
    def setUp(self):
        super().setUp()
        self.anna = make_user('anna', city='Москва', discoverable=True)
        self.anna_client = APIClient()
        self.anna_client.force_authenticate(self.anna)

    def block(self, client, username):
        return client.post('/api/blocks/', {'username': username}, format='json')

    def test_block_list_and_unblock(self):
        self.assertEqual(self.block(self.client, 'anna').status_code, 201)
        self.assertEqual(self.block(self.client, 'anna').json()['message'], 'anna уже в чёрном списке')
        listed = self.client.get('/api/blocks/').json()
        self.assertEqual([b['username'] for b in listed], ['anna'])
        self.assertEqual(self.client.delete(f'/api/blocks/{self.anna.pk}/').status_code, 200)
        self.assertEqual(self.client.get('/api/blocks/').json(), [])
        self.assertEqual(self.client.delete(f'/api/blocks/{self.anna.pk}/').status_code, 404)

    def test_cannot_block_self(self):
        self.assertEqual(self.block(self.client, 'me').status_code, 400)

    def test_blocked_people_disappear_from_search_both_ways(self):
        self.block(self.anna_client, 'me')
        self.assertEqual(self.client.get('/api/people/').json()['results'], [])
        self.assertEqual(self.client.get(f'/api/people/{self.anna.pk}/').status_code, 404)

    def test_blocker_sees_flag_in_profile(self):
        self.block(self.client, 'anna')
        # Заблокированный не ищется, но карточку заблокировавший открыть может
        self.assertTrue(self.client.get(f'/api/people/{self.anna.pk}/').json()['is_blocked'])

    def test_direct_chat_closed_both_ways(self):
        chat_id = self.client.post('/api/chats/', {'usernames': ['anna']}, format='json').json()['id']
        self.block(self.anna_client, 'me')

        for client in (self.client, self.anna_client):
            response = client.post(f'/api/chats/{chat_id}/messages/', {'text': 'hi'}, format='json')
            self.assertEqual(response.status_code, 403)

        statuses = {
            'me': self.client.get('/api/chats/').json()['results'][0]['block_status'],
            'anna': self.anna_client.get('/api/chats/').json()['results'][0]['block_status'],
        }
        self.assertEqual(statuses, {'me': 'blocked_me', 'anna': 'blocked_by_me'})
        # Историю открыть можно
        self.assertEqual(self.client.get(f'/api/chats/{chat_id}/messages/').status_code, 200)

    def test_new_direct_chat_refused(self):
        self.block(self.anna_client, 'me')
        response = self.client.post('/api/chats/', {'usernames': ['anna']}, format='json')
        self.assertEqual(response.status_code, 403)
        response = self.client.post('/api/chats/', {'usernames': ['anna', 'ivan'], 'title': 'Т'},
                                    format='json')
        self.assertEqual(response.status_code, 400)  # ivan не существует

    def test_group_messages_from_blocked_are_hidden(self):
        ivan = make_user('ivan')
        chat = services.create_group_chat(self.me, 'Т', [self.anna, ivan])
        services.send_message(chat, self.anna, 'Текст Анны')
        self.block(self.client, 'anna')

        thread = self.client.get(f'/api/chats/{chat.pk}/messages/').json()['results']
        self.assertEqual(thread[0]['text'], 'Сообщение от пользователя из вашего чёрного списка')
        self.assertTrue(thread[0]['is_hidden'])
        ivan_client = APIClient()
        ivan_client.force_authenticate(ivan)
        self.assertEqual(ivan_client.get(f'/api/chats/{chat.pk}/messages/').json()['results'][0]['text'],
                         'Текст Анны')
        # Непрочитанное от заблокированного не считается
        self.assertEqual(self.client.get('/api/chats/').json()['results'][0]['unread_count'], 0)

    def test_erasure_removes_blocks(self):
        self.block(self.client, 'anna')
        services.erase_user_social_data(self.anna)
        self.assertFalse(services.UserBlock.objects.exists())


class ReportTests(SocialTestCase):
    def setUp(self):
        super().setUp()
        self.anna = make_user('anna')
        self.chat = services.create_group_chat(
            self.anna, 'Т', [self.me] + [make_user(f'u{i}') for i in range(3)],
        )
        self.message = services.send_message(self.chat, self.anna, 'Купи слона')

    def report(self, client, **data):
        payload = {'reason': 'spam', **data}
        return client.post(f'/api/chats/{self.chat.pk}/messages/{self.message.pk}/report/',
                           payload, format='json')

    def client_for(self, username):
        client = APIClient()
        client.force_authenticate(User.objects.get(username=username))
        return client

    def test_report_saves_snapshot(self):
        response = self.report(self.client, comment='навязчиво')
        self.assertEqual(response.status_code, 201, response.content)
        report = services.MessageReport.objects.get()
        self.assertEqual((report.message_text, report.sender, report.reason), ('Купи слона', self.anna, 'spam'))

    def test_report_once_and_not_own(self):
        self.report(self.client)
        self.assertEqual(self.report(self.client).status_code, 400)
        self.assertEqual(self.report(self.client_for('anna')).status_code, 400)

    def test_outsider_cannot_report(self):
        make_user('stranger')
        self.assertEqual(self.report(self.client_for('stranger')).status_code, 404)

    def test_bad_reason(self):
        self.assertEqual(self.report(self.client, reason='boring').status_code, 400)

    def test_report_and_block(self):
        response = self.report(self.client, block=True)
        self.assertIn('чёрный список', response.json()['message'])
        self.assertTrue(services.UserBlock.objects.filter(blocker=self.me, blocked=self.anna).exists())

    def test_three_reports_hide_message_until_moderation(self):
        self.report(self.client)
        self.report(self.client_for('u0'))
        self.message.refresh_from_db()
        self.assertFalse(self.message.is_hidden)
        self.report(self.client_for('u1'))
        self.message.refresh_from_db()
        self.assertTrue(self.message.is_hidden)
        text = self.client_for('u2').get(f'/api/chats/{self.chat.pk}/messages/').json()['results'][0]['text']
        self.assertEqual(text, 'Сообщение скрыто модератором')

        moderator = User.objects.create_superuser('mod', 'mod@example.com', 'Sunrise-Harbor-42')
        services.resolve_reports(self.message, moderator, accept=False)
        self.message.refresh_from_db()
        self.assertFalse(self.message.is_hidden)
        self.assertEqual(
            set(services.MessageReport.objects.values_list('status', flat=True)), {'rejected'},
        )

    def test_admin_accept_action(self):
        self.report(self.client)
        moderator = User.objects.create_superuser('mod', 'mod@example.com', 'Sunrise-Harbor-42')
        web = self.client_class()
        web.force_login(moderator)
        report = services.MessageReport.objects.get()
        response = web.post('/admin/social/messagereport/', {
            'action': 'accept_reports', '_selected_action': [report.pk],
        })
        self.assertEqual(response.status_code, 302)
        self.message.refresh_from_db()
        self.assertTrue(self.message.is_hidden)


import io  # noqa: E402
import tempfile  # noqa: E402
from unittest import mock  # noqa: E402

from django.core.files.uploadedfile import SimpleUploadedFile  # noqa: E402
from PIL import Image  # noqa: E402

from main.storage import private_media_storage  # noqa: E402


def jpeg_with_gps(name='photo.jpg', size=(2400, 1200)):
    """Снимок с EXIF, в котором записаны координаты — как с телефона."""
    image = Image.new('RGB', size, (200, 100, 50))
    exif = Image.Exif()
    exif[0x8825] = {2: (55.0, 45.0, 0.0), 4: (37.0, 37.0, 0.0)}  # GPSInfo
    buffer = io.BytesIO()
    image.save(buffer, format='JPEG', exif=exif)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type='image/jpeg')


class ImageTests(SocialTestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        patches = [
            mock.patch.object(private_media_storage, 'location', self.tmp.name),
            mock.patch.object(private_media_storage, 'base_location', self.tmp.name),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        self.anna = make_user('anna', discoverable=True)
        self.anna_client = APIClient()
        self.anna_client.force_authenticate(self.anna)

    def stored_image(self, field):
        with field.open('rb') as handle:
            return Image.open(io.BytesIO(handle.read()))

    def test_avatar_is_square_small_and_without_exif(self):
        response = self.client.post('/api/profile/avatar/', {'image': jpeg_with_gps()}, format='multipart')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()['has_avatar'])
        image = self.stored_image(SocialProfile.objects.get(user=self.me).avatar)
        self.assertEqual(image.size, (512, 512))
        self.assertNotIn(0x8825, image.getexif())

    def test_not_an_image_rejected(self):
        fake = SimpleUploadedFile('x.jpg', b'not an image', content_type='image/jpeg')
        response = self.client.post('/api/profile/avatar/', {'image': fake}, format='multipart')
        self.assertEqual(response.status_code, 400)

    def test_avatar_visibility(self):
        self.anna_client.post('/api/profile/avatar/', {'image': jpeg_with_gps()}, format='multipart')
        # Анкета Анны открыта — фото видно
        response = self.client.get(f'/api/people/{self.anna.pk}/avatar/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/jpeg')
        # Анна заблокировала — не видно
        services.block_user(self.anna, self.me)
        self.assertEqual(self.client.get(f'/api/people/{self.anna.pk}/avatar/').status_code, 404)

    def test_hidden_profile_avatar_only_for_chat_partners(self):
        services.set_avatar(self.me, jpeg_with_gps())
        stranger = make_user('stranger')
        stranger_client = APIClient()
        stranger_client.force_authenticate(stranger)
        self.assertEqual(stranger_client.get(f'/api/people/{self.me.pk}/avatar/').status_code, 404)
        services.get_or_create_direct_chat(stranger, self.me)
        self.assertEqual(stranger_client.get(f'/api/people/{self.me.pk}/avatar/').status_code, 200)

    def test_photo_message(self):
        chat, _ = services.get_or_create_direct_chat(self.me, self.anna)
        response = self.client.post(f'/api/chats/{chat.pk}/messages/', {'image': jpeg_with_gps()},
                                    format='multipart')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertTrue(response.json()['has_image'])
        message_id = response.json()['id']
        image = self.stored_image(ChatMessage.objects.get(pk=message_id).image)
        self.assertEqual(max(image.size), 1600)
        self.assertNotIn(0x8825, image.getexif())

        url = f'/api/chats/{chat.pk}/messages/{message_id}/image/'
        self.assertEqual(self.anna_client.get(url).status_code, 200)
        outsider = APIClient()
        outsider.force_authenticate(make_user('outsider'))
        self.assertEqual(outsider.get(url).status_code, 404)

    def test_empty_message_still_rejected(self):
        chat, _ = services.get_or_create_direct_chat(self.me, self.anna)
        response = self.client.post(f'/api/chats/{chat.pk}/messages/', {'text': ''}, format='multipart')
        self.assertEqual(response.status_code, 400)

    def test_erasure_deletes_files(self):
        services.set_avatar(self.anna, jpeg_with_gps())
        chat, _ = services.get_or_create_direct_chat(self.me, self.anna)
        message = services.send_message(chat, self.anna, '', jpeg_with_gps())
        avatar_name = SocialProfile.objects.get(user=self.anna).avatar.name
        image_name = message.image.name
        services.erase_user_social_data(self.anna)
        self.assertFalse(private_media_storage.exists(avatar_name))
        self.assertFalse(private_media_storage.exists(image_name))
        thread = self.client.get(f'/api/chats/{chat.pk}/messages/').json()['results']
        self.assertEqual(thread[0]['text'], 'Сообщение удалено')
        self.assertFalse(thread[0]['has_image'])


from django.test import override_settings  # noqa: E402

from social import push  # noqa: E402
from social.models import DeviceToken  # noqa: E402


@override_settings(FCM_PROJECT_ID='test-project', FCM_CREDENTIALS_FILE='/nonexistent.json')
class PushTests(SocialTestCase):
    def setUp(self):
        super().setUp()
        self.anna = make_user('anna')
        self.ivan = make_user('ivan')
        self.anna_client = APIClient()
        self.anna_client.force_authenticate(self.anna)
        self.anna_client.post('/api/devices/', {'token': 'anna-phone'}, format='json')
        DeviceToken.objects.create(user=self.ivan, token='ivan-phone')

    def deliveries(self, send, prepare=lambda: None):
        """Сообщение в групповой чат; возвращает, на какие токены ушли уведомления."""
        sent = []

        def fake_send(token, data, collapse_key):
            sent.append((token, data))
            return token != 'ivan-phone'  # токен Ивана «устарел»

        chat = services.create_group_chat(self.me, 'Т', [self.anna, self.ivan])
        prepare()
        with mock.patch.object(push, 'send_to_token', side_effect=fake_send), \
                mock.patch.object(push.threading, 'Thread') as thread:
            thread.side_effect = lambda target, args, daemon: mock.Mock(start=lambda: target(*args))
            with self.captureOnCommitCallbacks(execute=True):
                send(chat)
        return chat, sent

    def test_only_chat_id_leaves_the_server(self):
        chat, sent = self.deliveries(lambda chat: services.send_message(chat, self.me, 'Секретный текст'))
        self.assertEqual(sorted(token for token, _ in sent), ['anna-phone', 'ivan-phone'])
        for _, data in sent:
            self.assertEqual(data, {'type': 'message', 'chat_id': chat.pk})
        # Недействительный токен забыт
        self.assertFalse(DeviceToken.objects.filter(token='ivan-phone').exists())

    def test_blockers_and_sender_not_notified(self):
        _, sent = self.deliveries(
            lambda chat: services.send_message(chat, self.me, 'Привет'),
            prepare=lambda: services.block_user(self.ivan, self.me),
        )
        self.assertEqual([token for token, _ in sent], ['anna-phone'])

    @override_settings(FCM_PROJECT_ID='')
    def test_disabled_without_settings(self):
        _, sent = self.deliveries(lambda chat: services.send_message(chat, self.me, 'Привет'))
        self.assertEqual(sent, [])

    def test_token_moves_to_new_account_and_unregisters(self):
        self.client.post('/api/devices/', {'token': 'anna-phone'}, format='json')
        self.assertEqual(DeviceToken.objects.get(token='anna-phone').user, self.me)
        self.client.post('/api/devices/unregister/', {'token': 'anna-phone'}, format='json')
        self.assertFalse(DeviceToken.objects.filter(token='anna-phone').exists())

    def test_empty_token_rejected(self):
        self.assertEqual(self.client.post('/api/devices/', {'token': ''}, format='json').status_code, 400)
