package ru.angelhelper.app.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp

/**
 * Мелкие части, из которых собраны все экраны.
 *
 * Собраны в одном месте не ради красоты: одинаковые поля и кнопки
 * означают, что исправление отступа или высоты нажатия делается один раз
 * и доходит до всех двенадцати экранов сразу.
 */

/** Поле ввода на всю ширину. Высота не меньше 56 — под палец. */
@Composable
fun Field(
    value: String,
    onValueChange: (String) -> Unit,
    label: String,
    modifier: Modifier = Modifier,
    keyboardType: KeyboardType = KeyboardType.Text,
    password: Boolean = false,
    singleLine: Boolean = true,
    supporting: String? = null,
) {
    OutlinedTextField(
        value = value,
        onValueChange = onValueChange,
        label = { Text(label) },
        singleLine = singleLine,
        // Клавиатура под тип поля: для суммы — цифровая, для почты —
        // с собакой. Иначе человек вводит сумму буквенной клавиатурой.
        keyboardOptions = KeyboardOptions(keyboardType = keyboardType),
        visualTransformation = if (password) {
            PasswordVisualTransformation()
        } else {
            androidx.compose.ui.text.input.VisualTransformation.None
        },
        supportingText = supporting?.let { { Text(it) } },
        modifier = modifier
            .fillMaxWidth()
            .heightIn(min = 56.dp),
    )
}

/** Главная кнопка экрана: во всю ширину и не ниже 48 точек. */
@Composable
fun PrimaryButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    busy: Boolean = false,
) {
    Button(
        onClick = onClick,
        enabled = enabled && !busy,
        modifier = modifier
            .fillMaxWidth()
            .heightIn(min = 48.dp),
    ) {
        if (busy) {
            CircularProgressIndicator(
                strokeWidth = 2.dp,
                modifier = Modifier.size(18.dp),
                color = MaterialTheme.colorScheme.onPrimary,
            )
            Spacer(Modifier.width(10.dp))
            Text(text)
        } else {
            Text(text)
        }
    }
}

@Composable
fun SecondaryButton(text: String, onClick: () -> Unit, modifier: Modifier = Modifier) {
    TextButton(onClick = onClick, modifier = modifier.fillMaxWidth()) {
        Text(text)
    }
}

/** Карточка с заголовком — основной блок на всех экранах. */
@Composable
fun Panel(
    title: String? = null,
    modifier: Modifier = Modifier,
    content: @Composable () -> Unit,
) {
    Card(
        modifier = modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
    ) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            if (title != null) {
                Text(title, style = MaterialTheme.typography.titleMedium)
            }
            content()
        }
    }
}

/** Строка «подпись — значение». Значение прижато вправо и переносится. */
@Composable
fun LabelValue(label: String, value: String, strong: Boolean = false) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.Top,
    ) {
        Text(
            label,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.weight(1f),
        )
        Text(
            value,
            style = MaterialTheme.typography.bodyMedium,
            fontWeight = if (strong) FontWeight.Bold else FontWeight.Normal,
            modifier = Modifier.weight(1f),
        )
    }
}

/**
 * Заметное предупреждение о том, что экран — заглушка.
 *
 * Стоит на каждом экране, за которым нет серверной операции. Без такой
 * пометки тестовое приложение врёт: человек видит «заявка создана» и
 * решает, что деньги действительно ушли.
 */
@Composable
fun StubBanner(text: String) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceVariant,
        ),
    ) {
        Text(
            text,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(14.dp),
        )
    }
}

/** Пустое состояние списка: объясняет, почему пусто, а не молчит. */
@Composable
fun EmptyNote(text: String) {
    Text(
        text,
        style = MaterialTheme.typography.bodyMedium,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(vertical = 24.dp),
    )
}

/** Сумма крупно. */
@Composable
fun Money(value: String, modifier: Modifier = Modifier) {
    Text(
        "$value ₽",
        style = MaterialTheme.typography.headlineSmall,
        color = MaterialTheme.colorScheme.primary,
        modifier = modifier,
    )
}
