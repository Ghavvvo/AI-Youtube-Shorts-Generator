"""Config de los 6 caption styles (puerto de ai-video-captions).

Cada estilo: fuente, colores (formato ASS &HBGR&) y tipo de animación.
La palabra activa se resalta con HIGHLIGHT; el resto usa PRIMARY.
"""


# Santa fuente por estilo (instaladas en ~/.fonts)
STYLES = {
    "hormozi": {
        "name": "Hormozi",
        "font": "Montserrat",
        "primary": "&H00FFFFFF&",       # blanco
        "highlight": "&H00FFFF00&",     # cian #00FFFF → BGR
        "outline": "&H00000000&",
        "shadow": "&H80000000&",
        "outline_size": 5.0,
        "shadow_depth": 4.5,
        "bold": True,
        "animation": "highlight",
        "font_size": 0.055,
    },
    "mrbeast": {
        "name": "MrBeast",
        "font": "Bebas Neue",
        "primary": "&H0000FFFF&",       # amarillo #FFFF00 → BGR
        "highlight": "&H0066FF00&",     # naranja #FF6600 → BGR
        "outline": "&H00000000&",
        "shadow": "&H00000000&",        # sin sombra (alpha 0)
        "outline_size": 8.0,
        "shadow_depth": 6.0,
        "bold": True,
        "animation": "highlight",
        "font_size": 0.062,
    },
    "karaoke": {
        "name": "Karaoke",
        "font": "Montserrat",
        "primary": "&H00FFFFFF&",
        "highlight": "&H00FF8000&",     # azul #0080FF → BGR
        "outline": "&H00000000&",
        "shadow": "&H80000000&",
        "outline_size": 4.0,
        "shadow_depth": 3.0,
        "bold": True,
        "animation": "karaoke",
        "font_size": 0.055,
    },
    "minimal": {
        "name": "Minimal",
        "font": "Bebas Neue",
        "primary": "&H00FFFFFF&",
        "highlight": "&H00F5F5F5&",
        "outline": "&H00000000&",
        "shadow": "&H80000000&",
        "outline_size": 4.0,
        "shadow_depth": 3.0,
        "bold": True,
        "italic": True,
        "animation": "scale",
        "font_size": 0.062,
    },
    "bounce": {
        "name": "Bounce",
        "font": "Bangers",
        "primary": "&H0088FF00&",       # verde #00FF88 → BGR
        "highlight": "&H00FF00FF&",     # magenta #FF00FF → BGR
        "outline": "&H00000000&",
        "shadow": "&H00000000&",
        "outline_size": 5.0,
        "shadow_depth": 5.0,
        "bold": True,
        "animation": "bounce",
        "font_size": 0.058,
    },
    "classic": {
        "name": "Classic",
        "font": "Anton",
        "primary": "&H00FFFFFF&",
        "highlight": "&H0000FFFF&",     # amarillo #FFFF00 → BGR
        "outline": "&H00000000&",
        "shadow": "&HB4000000&",
        "outline_size": 6.0,
        "shadow_depth": 3.0,
        "bold": True,
        "animation": "highlight",
        "font_size": 0.055,
    },
}

DEFAULT_STYLE = "hormozi"


def get_style(style_id: str) -> dict:
    return STYLES.get(style_id, STYLES[DEFAULT_STYLE])


def style_choices() -> list:
    return sorted(STYLES.keys())