"""A catalogue of disciplines across the map of knowledge, for Converge.

When the owner names fewer fields than they asked for, the rest are spread
evenly across these domains so the circle holds distant fields, not near
neighbours. No model is called and nothing here is a finding.
"""

from __future__ import annotations

import re

DOMAINS: dict[str, tuple[str, ...]] = {
    "Physics": (
        "Statistical mechanics",
        "Condensed matter physics",
        "Quantum information",
        "Fluid dynamics",
        "Optics",
        "Particle physics",
        "Thermodynamics",
        "Plasma physics",
        "Acoustics",
        "Nonlinear dynamics",
        "General relativity",
        "Soft matter physics",
    ),
    "Chemistry": (
        "Physical chemistry",
        "Catalysis",
        "Polymer chemistry",
        "Electrochemistry",
        "Supramolecular chemistry",
        "Chemical kinetics",
        "Crystallography",
        "Photochemistry",
        "Green chemistry",
        "Organic synthesis",
    ),
    "Life sciences": (
        "Molecular biology",
        "Evolutionary biology",
        "Ecology",
        "Neuroscience",
        "Immunology",
        "Developmental biology",
        "Microbiology",
        "Genetics",
        "Cell biology",
        "Systems biology",
        "Plant biology",
        "Animal behaviour",
        "Epigenetics",
        "Bioinformatics",
        "Physiology",
        "Marine biology",
        "Mycology",
        "Virology",
    ),
    "Medicine and health": (
        "Epidemiology",
        "Pharmacology",
        "Oncology",
        "Cardiology",
        "Public health",
        "Psychiatry",
        "Endocrinology",
        "Sleep medicine",
        "Gerontology",
        "Anaesthesiology",
        "Infectious disease",
        "Nutrition science",
    ),
    "Earth and space": (
        "Climate science",
        "Geology",
        "Seismology",
        "Oceanography",
        "Hydrology",
        "Atmospheric science",
        "Glaciology",
        "Volcanology",
        "Astrophysics",
        "Cosmology",
        "Planetary science",
        "Soil science",
    ),
    "Mathematics": (
        "Graph theory",
        "Topology",
        "Probability theory",
        "Number theory",
        "Category theory",
        "Dynamical systems",
        "Game theory",
        "Information theory",
        "Optimisation",
        "Combinatorics",
        "Differential geometry",
        "Logic",
        "Statistics",
        "Control theory",
    ),
    "Computing": (
        "Machine learning",
        "Distributed systems",
        "Cryptography",
        "Computer networks",
        "Compilers",
        "Databases",
        "Computational complexity",
        "Robotics",
        "Computer vision",
        "Natural language processing",
        "Error-correcting codes",
        "Human-computer interaction",
        "Computer security",
        "Reinforcement learning",
        "Quantum computing",
    ),
    "Engineering": (
        "Materials science",
        "Structural engineering",
        "Electrical power systems",
        "Signal processing",
        "Aerospace engineering",
        "Chemical engineering",
        "Civil engineering",
        "Mechanical engineering",
        "Biomedical engineering",
        "Nanotechnology",
        "Energy storage",
        "Telecommunications",
        "Manufacturing",
        "Transport engineering",
        "Tribology",
    ),
    "Mind and behaviour": (
        "Cognitive psychology",
        "Developmental psychology",
        "Social psychology",
        "Behavioural economics",
        "Linguistics",
        "Psycholinguistics",
        "Decision science",
        "Perception science",
        "Memory research",
        "Cognitive science",
    ),
    "Society": (
        "Economics",
        "Sociology",
        "Political science",
        "Anthropology",
        "Demography",
        "Urban studies",
        "Law",
        "Criminology",
        "Education research",
        "Organisational studies",
        "Network science",
        "Public policy",
        "Finance",
        "Marketing science",
        "Archaeology",
        "Geography",
        "International relations",
        "Labour economics",
    ),
    "Humanities and arts": (
        "Philosophy of science",
        "Ethics",
        "History of science",
        "Musicology",
        "Architecture",
        "Art history",
        "Literary studies",
        "Film studies",
        "Design",
        "Aesthetics",
        "Theology",
        "Classics",
        "Philosophy of mind",
        "Rhetoric",
        "Typography",
        "Dance studies",
    ),
    "Living systems at scale": (
        "Agriculture",
        "Forestry",
        "Fisheries science",
        "Conservation biology",
        "Food science",
        "Veterinary science",
        "Entomology",
        "Biogeography",
        "Restoration ecology",
        "Animal husbandry",
    ),
    "Information and media": (
        "Library science",
        "Journalism studies",
        "Media studies",
        "Information science",
        "Science communication",
        "Knowledge management",
        "Cartography",
        "Semiotics",
    ),
    "Complex systems": (
        "Complexity science",
        "Cybernetics",
        "Systems engineering",
        "Operations research",
        "Supply chain management",
        "Risk analysis",
        "Synthetic biology",
        "Artificial life",
        "Swarm intelligence",
        "Self-organisation",
        "Collective behaviour",
    ),
    "Sport and the body": (
        "Biomechanics",
        "Exercise physiology",
        "Sports science",
        "Ergonomics",
        "Motor control",
        "Kinesiology",
    ),
    "Crossings": (
        "Astrobiology",
        "Neuroeconomics",
        "Computational social science",
        "Quantum biology",
        "Biophysics",
        "Geochemistry",
        "Palaeontology",
        "Linguistic typology",
        "Music cognition",
        "Origami mathematics",
        "Metamaterials",
        "Biomimetics",
        "Chronobiology",
        "Glycobiology",
        "Astrochemistry",
    ),
}

CATALOGUE: tuple[str, ...] = tuple(name for names in DOMAINS.values() for name in names)
MAX_FIELD_NAME = 100


def normalise(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.casefold()).strip()


def clean(name: object) -> str | None:
    """One field name as the owner typed it, or None when it is not a usable name."""
    if not isinstance(name, str):
        return None
    value = " ".join(name.split())
    if not 2 <= len(value) <= MAX_FIELD_NAME or any(ord(character) < 32 for character in value):
        return None
    return value if normalise(value) else None


def spread(chosen: list[str], total: int) -> list[str]:
    """Fill to `total` fields by taking turns across domains, skipping chosen ones."""
    taken = {normalise(name) for name in chosen}
    queues = [
        [name for name in names if normalise(name) not in taken] for names in DOMAINS.values()
    ]
    picked: list[str] = []
    while len(chosen) + len(picked) < total and any(queues):
        for queue in queues:
            if queue and len(chosen) + len(picked) < total:
                picked.append(queue.pop(0))
    return picked


def suggest(query: str, limit: int = 12) -> list[str]:
    """Catalogue names that start with, then contain, the query."""
    key = normalise(query)
    if not key:
        return []
    starts = [name for name in CATALOGUE if normalise(name).startswith(key)]
    contains = [name for name in CATALOGUE if key in normalise(name) and name not in starts]
    return (starts + contains)[:limit]


def match(name: str, fields: list[str]) -> int | None:
    """The index of the circle field a model-named field refers to, if any."""
    key = normalise(name)
    if not key:
        return None
    keys = [normalise(field) for field in fields]
    if key in keys:
        return keys.index(key)
    for index, other in enumerate(keys):
        if min(len(key), len(other)) >= 4 and (key in other or other in key):
            return index
    return None
