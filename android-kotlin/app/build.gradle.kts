plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

// Push-уведомления требуют google-services.json из консоли Firebase.
// Файл личный для проекта и в репозиторий не кладётся; без него плагин
// не подключается, приложение собирается и работает — просто без push
// (Push.isAvailable вернёт false, и регистрация телефона не начнётся).
if (file("google-services.json").exists()) {
    apply(plugin = "com.google.gms.google-services")
}

// Версия и подпись приходят из окружения: их задаёт workflow выпуска
// (.github/workflows/release.yml) по тегу vX.Y.Z. Локальная сборка без
// переменных получает версию 1.0.0 (код 1) и не подписывается ключом
// выпуска — release-сборку тогда можно собрать, но не установить.
fun env(name: String): String? = System.getenv(name)?.takeIf { it.isNotBlank() }

// Адрес боевого сервера для release-сборки. Отладочная по-прежнему
// смотрит на 127.0.0.1 — под adb reverse, как описано в README.
val productionApiUrl = env("PRODUCTION_API_URL") ?: "https://angel-helper.ru/"

android {
    namespace = "ru.angelhelper.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "ru.angelhelper.app"
        minSdk = 24
        targetSdk = 35
        // Код версии растёт с каждым выпуском: RuStore и Google Play не
        // принимают сборку с кодом не больше уже опубликованного
        versionCode = env("VERSION_CODE")?.toInt() ?: 1
        versionName = env("VERSION_NAME") ?: "1.0.0"
    }

    signingConfigs {
        // Ключ выпуска: файл и пароли — из секретов GitHub. Ключ один на
        // всю жизнь приложения: потерянный ключ означает, что обновления
        // больше не поставятся поверх установленной версии.
        val keystore = env("ANDROID_KEYSTORE_PATH")
        if (keystore != null) {
            create("release") {
                storeFile = file(keystore)
                storePassword = env("ANDROID_KEYSTORE_PASSWORD")
                keyAlias = env("ANDROID_KEY_ALIAS")
                keyPassword = env("ANDROID_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        debug {
            // Отдельный суффикс, чтобы тестовая сборка и будущая рабочая
            // могли стоять на одном телефоне одновременно
            applicationIdSuffix = ".debug"
            isMinifyEnabled = false
            buildConfigField("String", "DEFAULT_BASE_URL", "\"http://127.0.0.1:8000/\"")
        }
        release {
            signingConfig = signingConfigs.findByName("release")
            buildConfigField("String", "DEFAULT_BASE_URL", "\"$productionApiUrl\"")
            // Для тестового приложения сжатие выключено намеренно:
            // оно удлиняет сборку и запутывает стек вызовов в отчётах
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        compose = true
        // BuildConfig.DEFAULT_BASE_URL и BuildConfig.DEBUG
        buildConfig = true
    }

    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
        }
    }
}

// Зависимостей намеренно мало.
//
// Ни Retrofit, ни Moshi, ни kotlinx.serialization, ни Navigation здесь
// нет: HTTP делается на HttpURLConnection, разбор ответов — на org.json,
// переходы между экранами — своим стеком в модели. Всё перечисленное
// входит в саму платформу Android, то есть не может разойтись по версиям
// и не требует ничего скачивать. Для приложения, которое собирают на
// чужой машине и раз в неделю, это важнее удобства.
dependencies {
    implementation("androidx.core:core-ktx:1.19.1")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.7")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.7")
    // viewModelScope и AndroidViewModel живут здесь. Приходят они и
    // транзитивно, но объявлены явно: подтянувшаяся «сама собой»
    // зависимость исчезает при обновлении соседней, и сборка ломается
    // в месте, которое никто не менял.
    implementation("androidx.lifecycle:lifecycle-viewmodel-ktx:2.8.7")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
    implementation("androidx.activity:activity-compose:1.9.3")

    implementation(platform("androidx.compose:compose-bom:2024.10.01"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-graphics")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    // Значки приходят к material3 транзитивно, но в Compose 1.4 эту
    // зависимость убирают. Объявляем явно по тому же доводу, что и
    // lifecycle-viewmodel-ktx выше.
    implementation("androidx.compose.material:material-icons-core")

    debugImplementation("androidx.compose.ui:ui-tooling")

    // Юнит-тесты (app/src/test): правила демо-режима, склейка страниц,
    // модели. Выполняются на JVM без эмулятора: ./gradlew testDebugUnitTest
    testImplementation("junit:junit:4.13.2")

    // Push-уведомления. Единственная зависимость не из AndroidX: доставку
    // на Android без собственного постоянного соединения делает только
    // Firebase Cloud Messaging. Текст сообщений через него не идёт —
    // только номер чата (см. push/PushService.kt).
    implementation(platform("com.google.firebase:firebase-bom:33.7.0"))
    implementation("com.google.firebase:firebase-messaging")
}
