// Плагины объявлены без apply: версии задаются в одном месте, а
// подключаются уже в модуле app.
plugins {
    id("com.android.application") version "8.7.2" apply false
    id("org.jetbrains.kotlin.android") version "2.0.21" apply false
    // Начиная с Kotlin 2.0 компилятор Compose поставляется отдельным
    // плагином и версией совпадает с Kotlin. Раньше приходилось вручную
    // держать соответствие kotlinCompilerExtensionVersion — именно на
    // этом чаще всего и ломалась сборка при обновлении Kotlin.
    id("org.jetbrains.kotlin.plugin.compose") version "2.0.21" apply false
    // Firebase: push-уведомления. Подключается в app только при наличии
    // app/google-services.json — без него сборка идёт как раньше.
    id("com.google.gms.google-services") version "4.5.0" apply false
}
