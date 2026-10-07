@rem Запуск сборки через обёртку Gradle (Windows).
@rem Укороченная версия стандартного gradlew.bat.
@echo off
setlocal
@rem Кодовая страница UTF-8: иначе русский текст ниже выводится кракозябрами
chcp 65001 > nul
set DIRNAME=%~dp0
set WRAPPER_JAR=%DIRNAME%gradle\wrapper\gradle-wrapper.jar
if not exist "%WRAPPER_JAR%" (
  echo Не найден %WRAPPER_JAR% 1^>^&2
  exit /b 1
)
if defined JAVA_HOME (set JAVACMD=%JAVA_HOME%\bin\java.exe) else (set JAVACMD=java)
"%JAVACMD%" -Xmx64m -Xms64m -classpath "%WRAPPER_JAR%" org.gradle.wrapper.GradleWrapperMain %*
@rem Код возврата обязателен: без него упавшая сборка на Windows-CI
@rem отмечается успешной
exit /b %ERRORLEVEL%
