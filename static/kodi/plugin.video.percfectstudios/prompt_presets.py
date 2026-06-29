import random

PRINCESSES = [
    ('Ariel', 'Ariel-inspired mermaid princess, flowing vibrant red hair, seashell crown, ocean sparkle'),
    ('Belle', 'Belle-inspired princess, golden ball gown, warm brown hair, elegant bookish charm'),
    ('Jasmine', 'Jasmine-inspired princess, teal outfit, long black hair, Arabian palace aesthetic'),
    ('Rapunzel', 'Rapunzel-inspired princess, impossibly long golden braid, purple dress, lantern glow'),
    ('Elsa', 'Elsa-inspired ice queen princess, shimmering blue gown, snowflake magic, platinum braid'),
    ('Aurora', 'Aurora-inspired sleeping beauty princess, pink flowing gown, soft rose-gold hair'),
    ('Cinderella', 'Cinderella-inspired princess, sparkling blue ball gown, glass slippers, midnight magic'),
    ('Moana', 'Moana-inspired island princess, ocean waves, bold spirit, tropical flower accents'),
    ('Tiana', 'Tiana-inspired princess, emerald green gown, New Orleans elegance, warm golden lighting'),
    ('Mulan', 'Mulan-inspired warrior princess, crimson and gold, fierce grace, cherry blossom petals'),
]

OUTFITS = [
    ('Slit gown', 'high-slit silk gown, bare thigh, plunging neckline'),
    ('Lingerie', 'delicate lace lingerie set, garter straps, sheer fabric'),
    ('Bikini', 'skimpy metallic bikini, wet glossy skin, beach glam'),
    ('Torn dress', 'torn royal gown, ripped fabric, exposed shoulder and leg'),
    ('Corset', 'tight lace corset, stockings, no skirt, bedroom lighting'),
    ('Sheer robe', 'sheer silk robe falling open, nothing underneath, candlelit'),
]

POSES = [
    ('Standing', 'standing full body portrait, hand on hip'),
    ('Throne', 'sitting on throne, legs crossed, regal posture'),
    ('Lying', 'lying on silk sheets, arched back, relaxed sensual pose'),
    ('Dancing', 'mid-dance pose, flowing hair and fabric motion'),
    ('Balcony', 'leaning on balcony railing, wind in hair'),
]

SCENES = [
    ('Ballroom', 'inside a grand fairy tale castle ballroom with crystal chandeliers'),
    ('Forest', 'enchanted forest clearing with glowing fireflies and magical mist'),
    ('Ocean', 'moonlit ocean shore with bioluminescent waves and starry sky'),
    ('Bedroom', 'luxurious canopy bed, silk sheets, warm candlelight'),
    ('Palace', 'dark palace balcony with stormy sky, cinematic lighting'),
]

EXPRESSIONS = [
    ('Seductive', 'seductive half-smile, bedroom eyes, parted lips'),
    ('Innocent', 'innocent wide eyes, soft blush, shy glance'),
    ('Confident', 'confident smirk, direct eye contact, powerful gaze'),
    ('Dreamy', 'dreamy distant gaze, soft romantic mood'),
]

STYLE = (
    'highly detailed 3D CGI render, cinematic volumetric lighting, ultra realistic skin texture, '
    '8k masterpiece, best quality, sharp focus, glossy skin'
)


def build_prompt(princess, outfit, pose, scene, expression, extra=''):
    parts = [STYLE, princess, f'wearing {outfit}', expression, pose, f'in {scene}']
    if extra:
        parts.append(extra)
    return ', '.join(parts)


def random_prompt():
    return build_prompt(
        random.choice(PRINCESSES)[1],
        random.choice(OUTFITS)[1],
        random.choice(POSES)[1],
        random.choice(SCENES)[1],
        random.choice(EXPRESSIONS)[1],
    )


def pick_from_list(title, options):
    import xbmcgui
    labels = [o[0] for o in options]
    idx = xbmcgui.Dialog().select(title, labels)
    if idx < 0:
        return None
    return options[idx][1]