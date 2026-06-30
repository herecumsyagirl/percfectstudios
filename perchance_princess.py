"""Prompt builder for perchance.org/o3m0yoyo03 (Perfect Princess Generator)."""
from __future__ import annotations

import random
from typing import Optional

PERCHANCE_URL = "https://perchance.org/o3m0yoyo03"
PERCHANCE_SLUG = "o3m0yoyo03"
DEFAULT_COUNT = 6
DEFAULT_SIZE = 960

NEG_PROMPT = (
    "blurry, low quality, deformed, bad anatomy, extra limbs, childlike, "
    "underage, text, watermark, cartoon, painting"
)

PROMPT_TEMPLATE = (
    "while {location}, wearing {outfit}, {princess} is {position} {partner} "
    "in an ultra-detailed, GTA 5 style, 3D Disney, concept art, masterpiece, "
    "best quality, 8k"
)

PRINCESSES = {
    "Jasmine": "Disney's slutty erotic princess Jasmine, Princess of Forbidden Desires",
    "Ariel": "Disney's slutty erotic princess Ariel the little red headed mermaid",
    "Belle": "Disney's slutty erotic princess Belle the Bound Beauty",
    "Pocahontas": (
        "Disney's slutty erotic princess Pocahontas the savage native american "
        "feather in hair, indian whore"
    ),
    "Megara": "Disney's slutty erotic Meg from Hercules",
    "Esmeralda": "Disney's slutty erotic princess Esmeralda the gypsy slut",
    "Mulan": "Disney's slutty erotic asian princess Mulan",
    "Elsa": "Disney's slutty erotic princess Elsa from 'Frozen' the Ice Queen of Cold Passion",
    "Rapunzel": (
        "Disney's slutty erotic princess Rapunzel the Tower-bound, green eyed, "
        "blonde extremely long hair (leash), Rapunzel"
    ),
    "Tiana": (
        "Disney's slutty erotic very tan black girl, cock loving, green wearing, "
        "whore princess Tiana"
    ),
    "Aurora": "Disney's slutty erotic princess Aurora aka 'sleeping beauty'",
}

LOCATIONS = [
    "inside the glittering Palace of Agrabah at night",
    "on top of Pride Rock during a dramatic sunset",
    "in the grand enchanted castle library with floating candles",
    "in the mystical underwater grotto of Atlantica",
    "inside the towering Notre Dame bell tower at midnight",
    "deep in the lush jungle treehouse",
    "inside the treasure-filled Cave of Wonders",
    "aboard a magical flying carpet high above the desert",
    "in a kiddie pool",
    "on an office desk",
    "in the castle",
]

OUTFITS = [
    "wearing a sheer slit-to-the-hip version of her classic outfit with nothing underneath",
    "in a torn and revealing princess gown with breasts spilling out",
    "nothing at all completely naked except for her spiked collar and dog leash",
    "g string polka dotted micro bikini",
    "translucent glowing lingerie that clings to every curve",
    "leather boobless bodysuit with thigh-high boots",
    "covered in degrading quotes written in marker",
    "sex slave outfit with shackles and chains",
    "black leather straps",
    "3 pieces of electrical tape in form of an X as nipple pasties and vagina covering",
]

POSITIONS = [
    "riding cock passionately in cowgirl position with",
    "being fucked hard in missionary position by",
    "taken roughly from behind in doggy style position by",
    "getting pounded deep anally with cum and blood dripping from her asshole by",
    "held intensely sucking dick and gagging with fluid leaking from her lips while getting facefucked by",
    "sitting riding face aggressively on top of",
    "lifted and fucked in carry position by",
    "passionately scissoring with another princess while watched by",
    "69ing deeply with another princess while watched by",
    "eating out another princess while getting fucked by",
]

PARTNERS = [
    "Jafar the dark sorcerer",
    "Hades Lord of the Underworld",
    "Ursula the squid woman",
    "Gaston the arrogant hunter",
    "Judge Claude Frollo the priest",
    "The Beast from Beauty and the Beast",
    "Shan Yu the ruthless conqueror",
    "John Smith the Jamestown settler",
    "Captain Hook",
    "Mr. Smee",
]


def build_princess_prompt(princess_key: str, rng: Optional[random.Random] = None) -> str:
    """Build one random prompt matching the Perchance generator logic."""
    r = rng or random
    key = (princess_key or "Jasmine").strip()
    if key not in PRINCESSES:
        key = "Jasmine"
    return PROMPT_TEMPLATE.format(
        location=r.choice(LOCATIONS),
        outfit=r.choice(OUTFITS),
        princess=PRINCESSES[key],
        position=r.choice(POSITIONS),
        partner=r.choice(PARTNERS),
    )


def build_princess_prompts(princess_key: str, count: int = DEFAULT_COUNT) -> list[str]:
    count = max(1, min(6, int(count)))
    rng = random.Random()
    return [build_princess_prompt(princess_key, rng) for _ in range(count)]