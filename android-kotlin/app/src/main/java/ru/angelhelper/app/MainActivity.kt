package ru.angelhelper.app

import android.Manifest
import android.content.Intent
import android.os.Build
import android.os.Bundle
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import ru.angelhelper.app.push.PushRouter
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.List
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.Email
import androidx.compose.material.icons.filled.Face
import androidx.compose.material.icons.filled.Favorite
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.Badge
import androidx.compose.material3.BadgedBox
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import ru.angelhelper.app.ui.AngelsHeartTheme
import ru.angelhelper.app.ui.AppViewModel
import ru.angelhelper.app.ui.Screen
import ru.angelhelper.app.ui.UiState
import ru.angelhelper.app.ui.screens.BlockListScreen
import ru.angelhelper.app.ui.screens.ChatScreen
import ru.angelhelper.app.ui.screens.ChatsScreen
import ru.angelhelper.app.ui.screens.ConsentsScreen
import ru.angelhelper.app.ui.screens.GroupDetailScreen
import ru.angelhelper.app.ui.screens.GroupsScreen
import ru.angelhelper.app.ui.screens.NewChatScreen
import ru.angelhelper.app.ui.screens.NewGroupScreen
import ru.angelhelper.app.ui.screens.PeopleScreen
import ru.angelhelper.app.ui.screens.PersonDetailScreen
import ru.angelhelper.app.ui.screens.SocialProfileEditScreen
import ru.angelhelper.app.ui.screens.DashboardScreen
import ru.angelhelper.app.ui.screens.FundraiseDetailScreen
import ru.angelhelper.app.ui.screens.FundraisesScreen
import ru.angelhelper.app.ui.screens.HistoryScreen
import ru.angelhelper.app.ui.screens.LeadersScreen
import ru.angelhelper.app.ui.screens.LoginScreen
import ru.angelhelper.app.ui.screens.PasswordResetScreen
import ru.angelhelper.app.ui.screens.ProfileScreen
import ru.angelhelper.app.ui.screens.RegisterScreen
import ru.angelhelper.app.ui.screens.SettingsScreen
import ru.angelhelper.app.ui.screens.TopUpScreen
import ru.angelhelper.app.ui.screens.TransferScreen
import ru.angelhelper.app.ui.screens.VerificationScreen
import ru.angelhelper.app.ui.screens.WithdrawScreen

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Запуск из уведомления о сообщении: откроется нужный чат
        PushRouter.handle(intent)
        setContent {
            AngelsHeartTheme {
                AppRoot()
            }
        }
    }

    // launchMode=singleTop: нажатие на уведомление при открытом приложении
    // приходит сюда, а не создаёт вторую копию экрана
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        PushRouter.handle(intent)
    }
}

/**
 * Разделы нижней навигации: только те, что осмысленны после входа.
 *
 * Перевод и история ушли с панели, чтобы освободить место «Людям» и
 * «Чатам»: больше пяти разделов панель Material не вмещает без
 * обрезанных подписей. Обе операции остались на «Главной», а перевод —
 * ещё и в анкете человека и в личном чате.
 */
private val bottomScreens = listOf(
    Screen.Dashboard to "Главная",
    Screen.Fundraises to "Сборы",
    Screen.People to "Люди",
    Screen.Chats to "Чаты",
    Screen.Profile to "Профиль",
)

/** Вкладка «Люди» подсвечена и на соседнем экране групп. */
private fun isSelected(tab: Screen, current: Screen): Boolean =
    current == tab || (tab == Screen.People && current == Screen.Groups)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AppRoot(vm: AppViewModel = viewModel()) {
    val state by vm.state.collectAsState()
    val snackbar = remember { SnackbarHostState() }

    // Сообщения показываются полосой снизу и гаснут сами. Отдельного
    // окна здесь не нужно: ни одно из них не требует решения.
    LaunchedEffect(state.message?.id) {
        val message = state.message
        if (message != null) {
            snackbar.showSnackbar(message.text)
            vm.dismissMessage()
        }
    }

    // Переход из уведомления о сообщении
    val pendingChat by PushRouter.pendingChat.collectAsState()
    LaunchedEffect(pendingChat) {
        pendingChat?.let {
            vm.openChatFromNotification(it)
            PushRouter.consume()
        }
    }

    // Разрешение на уведомления (Android 13+) спрашивается один раз, уже
    // после входа: на экране входа вопрос «разрешить уведомления?» не
    // объясняет сам себя, а отказ потом не переспросить
    val askNotifications = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { vm.markNotificationsAsked() }
    val signedInScreen = state.screen == Screen.Dashboard
    LaunchedEffect(signedInScreen) {
        if (signedInScreen && !vm.notificationsAsked &&
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
        ) {
            vm.markNotificationsAsked()
            askNotifications.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }

    // Системная кнопка «назад» ходит по тому же стеку, что и стрелка в
    // шапке. Без этого она закрывала бы приложение с любого экрана.
    BackHandler(enabled = state.canGoBack) { vm.back() }

    // Нижняя панель ведёт в разделы, доступные только после входа.
    // Настройки в этот список тоже входят: шестерёнка видна и на экране
    // входа, и без этой строки с неё можно было провалиться в приложение
    // без токена — а вернуться ко входу оттуда было уже нечем.
    val authScreen = state.screen == Screen.Login ||
        state.screen == Screen.Register ||
        state.screen == Screen.PasswordReset ||
        state.screen == Screen.Settings

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(titleFor(state)) },
                navigationIcon = {
                    if (state.canGoBack) {
                        IconButton(onClick = { vm.back() }) {
                            Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Назад")
                        }
                    }
                },
                actions = {
                    // На самом экране настроек шестерёнку не показываем:
                    // нажатие клало бы Settings поверх Settings, и «назад»
                    // приходилось бы жать дважды
                    if (state.screen != Screen.Settings) {
                        IconButton(onClick = { vm.go(Screen.Settings) }) {
                            Icon(Icons.Default.Settings, contentDescription = "Настройки")
                        }
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.primary,
                    titleContentColor = MaterialTheme.colorScheme.onPrimary,
                    navigationIconContentColor = MaterialTheme.colorScheme.onPrimary,
                    actionIconContentColor = MaterialTheme.colorScheme.onPrimary,
                ),
            )
        },
        bottomBar = {
            if (!authScreen) {
                NavigationBar {
                    // Непрочитанные считаются по последнему загруженному списку
                    // чатов: отдельного опроса ради значка нет, чтобы не тратить
                    // лимит запросов, пока человек смотрит другие разделы
                    val unread = state.chats.sumOf { it.unreadCount }
                    bottomScreens.forEach { (screen, label) ->
                        NavigationBarItem(
                            selected = isSelected(screen, state.screen),
                            onClick = { vm.goRoot(screen) },
                            icon = {
                                if (screen == Screen.Chats && unread > 0) {
                                    BadgedBox(badge = { Badge { Text("$unread") } }) {
                                        Icon(iconFor(screen), contentDescription = label)
                                    }
                                } else {
                                    Icon(iconFor(screen), contentDescription = label)
                                }
                            },
                            label = { Text(label) },
                        )
                    }
                }
            }
        },
        snackbarHost = { SnackbarHost(snackbar) },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding),
        ) {
            // Полоса ожидания вместо кружка посреди экрана: содержимое
            // остаётся на месте и не прыгает при каждой загрузке
            if (state.busy) {
                LinearProgressIndicator(
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(3.dp)
                )
            }

            // Переписка листается своим списком с полем ввода внизу, поэтому
            // не может лежать в общей прокрутке: ленивый список внутри
            // verticalScroll падает с ошибкой о бесконечной высоте
            val openChat = state.screen as? Screen.Chat
            if (openChat != null) {
                Box(
                    modifier = Modifier
                        .fillMaxSize()
                        .padding(horizontal = 12.dp, vertical = 8.dp),
                ) {
                    ChatScreen(vm, state, openChat.id)
                }
            } else Column(
                modifier = Modifier
                    .fillMaxSize()
                    .verticalScroll(rememberScrollState())
                    .padding(horizontal = 16.dp, vertical = 12.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                when (val screen = state.screen) {
                    Screen.Login -> LoginScreen(vm, state)
                    Screen.Register -> RegisterScreen(vm, state)
                    Screen.PasswordReset -> PasswordResetScreen(vm, state)
                    Screen.Dashboard -> DashboardScreen(vm, state)
                    Screen.Fundraises -> FundraisesScreen(vm, state)
                    is Screen.FundraiseDetail -> FundraiseDetailScreen(vm, state, screen.id)
                    Screen.Transfer -> TransferScreen(vm, state)
                    Screen.History -> HistoryScreen(vm, state)
                    Screen.Profile -> ProfileScreen(vm, state)
                    Screen.Verification -> VerificationScreen(vm, state)
                    Screen.TopUp -> TopUpScreen(vm, state)
                    Screen.Withdraw -> WithdrawScreen(vm, state)
                    Screen.Leaders -> LeadersScreen(vm, state)
                    Screen.Consents -> ConsentsScreen(vm, state)
                    Screen.Settings -> SettingsScreen(vm, state)
                    Screen.People -> PeopleScreen(vm, state)
                    is Screen.PersonDetail -> PersonDetailScreen(vm, state, screen.id)
                    Screen.Groups -> GroupsScreen(vm, state)
                    is Screen.GroupDetail -> GroupDetailScreen(vm, state, screen.id)
                    Screen.NewGroup -> NewGroupScreen(vm, state)
                    Screen.Chats -> ChatsScreen(vm, state)
                    is Screen.Chat -> Unit // рисуется выше, вне общей прокрутки
                    Screen.NewChat -> NewChatScreen(vm, state)
                    Screen.SocialProfileEdit -> SocialProfileEditScreen(vm, state)
                    Screen.BlockList -> BlockListScreen(vm, state)
                }
            }
        }
    }
}

private fun titleFor(state: UiState): String = when (state.screen) {
    Screen.Login -> "Вход"
    Screen.Register -> "Регистрация"
    Screen.PasswordReset -> "Восстановление пароля"
    Screen.Dashboard -> "Ангел-Хранитель"
    Screen.Fundraises -> "Сборы средств"
    is Screen.FundraiseDetail -> "Сбор"
    Screen.Transfer -> "Перевод"
    Screen.History -> "История операций"
    Screen.Profile -> "Профиль"
    Screen.Verification -> "Верификация"
    Screen.TopUp -> "Пополнение"
    Screen.Withdraw -> "Вывод средств"
    Screen.Leaders -> "Лидеры"
    Screen.Consents -> "Мои согласия"
    Screen.Settings -> "Настройки"
    Screen.People -> "Люди"
    is Screen.PersonDetail -> "Анкета"
    Screen.Groups -> "Группы"
    is Screen.GroupDetail -> "Группа"
    Screen.NewGroup -> "Новая группа"
    Screen.Chats -> "Чаты"
    is Screen.Chat -> "Переписка"
    Screen.NewChat -> "Новый чат"
    Screen.SocialProfileEdit -> "Анкета в сообществе"
    Screen.BlockList -> "Чёрный список"
}

private fun iconFor(screen: Screen) = when (screen) {
    Screen.Fundraises -> Icons.Default.Favorite
    Screen.Transfer -> Icons.AutoMirrored.Filled.Send
    Screen.History -> Icons.AutoMirrored.Filled.List
    Screen.Profile -> Icons.Default.Person
    Screen.People -> Icons.Default.Face
    Screen.Chats -> Icons.Default.Email
    else -> Icons.Default.Home
}
