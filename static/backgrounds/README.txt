Arena background presets
========================

To turn a dropdown preset into a real backdrop image (characters get composited
onto it instead of just described in the prompt), drop a JPEG here named after
the preset key:

    static/backgrounds/<key>.jpg

Current preset keys (see ARENA_BACKGROUNDS in app.py):
    studio, arena, throne, neon, forest, beach, space, ring

Example: static/backgrounds/throne.jpg  ->  the "Throne Room" preset.

Recommended size: 1024x576 (16:9), landscape, no people in the scene.
If no image exists for a key, that preset just uses its text description.
