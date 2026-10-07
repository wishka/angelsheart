package ru.angelhelper.app.ui

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

/**
 * Цвета взяты с сайта: приложение и сайт — одно и то же для человека,
 * и разная палитра читалась бы как разные сервисы.
 *
 * Динамические цвета Android 12 сознательно не подключены: они
 * подстраиваются под обои телефона, и фирменный розовый превращался бы
 * в произвольный оттенок.
 */
private val Rose = Color(0xFFD4737A)
private val RoseDark = Color(0xFFC2646B)
private val RoseLight = Color(0xFFE8A4AA)
private val Cream = Color(0xFFFDF4F0)
private val Ink = Color(0xFF4A3B3B)
private val Muted = Color(0xFF6B5A5A)

private val LightColors = lightColorScheme(
    primary = Rose,
    onPrimary = Color.White,
    primaryContainer = Color(0xFFFFE0E3),
    onPrimaryContainer = Ink,
    secondary = RoseLight,
    onSecondary = Color.White,
    background = Cream,
    onBackground = Ink,
    surface = Color.White,
    onSurface = Ink,
    surfaceVariant = Color(0xFFFBEAE6),
    onSurfaceVariant = Muted,
    error = Color(0xFFB3261E),
    onError = Color.White,
    outline = Color(0xFFE0D0D0),
)

private val DarkColors = darkColorScheme(
    primary = RoseLight,
    onPrimary = Color(0xFF3A2326),
    primaryContainer = RoseDark,
    onPrimaryContainer = Color.White,
    secondary = Rose,
    onSecondary = Color.White,
    background = Color(0xFF231A1B),
    onBackground = Color(0xFFF2E7E7),
    surface = Color(0xFF2E2224),
    onSurface = Color(0xFFF2E7E7),
    surfaceVariant = Color(0xFF3A2C2E),
    onSurfaceVariant = Color(0xFFD5C2C2),
    outline = Color(0xFF574546),
)

private val AppTypography = Typography(
    headlineSmall = TextStyle(fontSize = 22.sp, fontWeight = FontWeight.Bold),
    titleLarge = TextStyle(fontSize = 19.sp, fontWeight = FontWeight.SemiBold),
    titleMedium = TextStyle(fontSize = 16.sp, fontWeight = FontWeight.SemiBold),
    bodyLarge = TextStyle(fontSize = 16.sp),
    bodyMedium = TextStyle(fontSize = 14.sp),
    labelLarge = TextStyle(fontSize = 15.sp, fontWeight = FontWeight.SemiBold),
)

@Composable
fun AngelsHeartTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit,
) {
    MaterialTheme(
        colorScheme = if (darkTheme) DarkColors else LightColors,
        typography = AppTypography,
        content = content,
    )
}
