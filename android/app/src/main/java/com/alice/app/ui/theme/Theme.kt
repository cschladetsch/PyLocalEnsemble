package com.alice.app.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

val DeepBlack = Color(0xFF0A0A0A)
val SurfaceDark = Color(0xFF141414)
val SurfaceVariantDark = Color(0xFF1E1E1E)
val MutedGold = Color(0xFFC8A96E)
val MutedGoldDim = Color(0xFF8A7249)
val OnSurfaceLight = Color(0xFFE8E0D0)
val OnSurfaceMuted = Color(0xFF9A9080)

private val AliceColorScheme = darkColorScheme(
    primary = MutedGold,
    onPrimary = DeepBlack,
    primaryContainer = MutedGoldDim,
    onPrimaryContainer = OnSurfaceLight,
    secondary = MutedGoldDim,
    onSecondary = OnSurfaceLight,
    background = DeepBlack,
    onBackground = OnSurfaceLight,
    surface = SurfaceDark,
    onSurface = OnSurfaceLight,
    surfaceVariant = SurfaceVariantDark,
    onSurfaceVariant = OnSurfaceMuted,
    outline = MutedGoldDim,
    error = Color(0xFFCF6679),
    onError = DeepBlack,
)

@Composable
fun AliceTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = AliceColorScheme,
        content = content
    )
}
