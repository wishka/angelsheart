// Репозитории объявлены здесь, а не в модулях: так их нельзя случайно
// развести по разным местам и получить разные версии одной библиотеки.
pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "AngelsHeart"
include(":app")
