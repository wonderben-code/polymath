"""A catalogue of important science and engineering fields, for Converge.

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
        "Quantum field theory",
        "Fluid dynamics",
        "Plasma physics",
        "Particle physics",
        "Thermodynamics",
        "Optics",
        "Nonlinear dynamics",
        "General relativity",
        "Soft matter physics",
        "Biophysics",
        "Acoustics",
    ),
    "Quantum technology": (
        "Quantum computing",
        "Quantum information",
        "Quantum sensing",
        "Quantum materials",
        "Quantum chemistry",
        "Quantum error correction",
        "Quantum communication",
        "Quantum biology",
    ),
    "Energy": (
        "Nuclear fusion",
        "Nuclear fission",
        "Photovoltaics",
        "Energy storage",
        "Battery chemistry",
        "Hydrogen energy",
        "Electrical power systems",
        "Superconductivity",
        "Carbon capture",
        "Geothermal energy",
        "Wind energy",
        "Thermoelectrics",
    ),
    "Materials": (
        "Materials science",
        "Two-dimensional materials",
        "Metamaterials",
        "Nanomaterials",
        "Polymer science",
        "Ceramics",
        "Catalysis",
        "Biomaterials",
        "Self-healing materials",
        "Metallurgy",
        "Crystallography",
        "Tribology",
    ),
    "Chemistry": (
        "Physical chemistry",
        "Electrochemistry",
        "Photochemistry",
        "Supramolecular chemistry",
        "Green chemistry",
        "Organic synthesis",
        "Chemical kinetics",
        "Computational chemistry",
        "Atmospheric chemistry",
        "Astrochemistry",
        "Geochemistry",
    ),
    "Life sciences": (
        "Ecology",
        "Molecular biology",
        "Evolutionary biology",
        "Neuroscience",
        "Immunology",
        "Developmental biology",
        "Microbiology",
        "Genetics",
        "Cell biology",
        "Systems biology",
        "Structural biology",
        "Epigenetics",
        "Plant biology",
        "Virology",
        "Animal behaviour",
        "Marine biology",
        "Chronobiology",
    ),
    "Biotechnology": (
        "Synthetic biology",
        "Gene editing",
        "Genomics",
        "Proteomics",
        "Protein design",
        "Bioinformatics",
        "Stem cell biology",
        "Regenerative medicine",
        "Tissue engineering",
        "Biology of ageing",
        "Microbiome science",
        "Bioprocess engineering",
        "Directed evolution",
    ),
    "Medicine": (
        "Epidemiology",
        "Pharmacology",
        "Drug discovery",
        "Oncology",
        "Immunotherapy",
        "Vaccinology",
        "Medical imaging",
        "Precision medicine",
        "Neurology",
        "Cardiology",
        "Infectious disease",
        "Public health",
        "Psychiatry",
        "Endocrinology",
        "Sleep medicine",
    ),
    "Earth and climate": (
        "Climate science",
        "Oceanography",
        "Atmospheric science",
        "Geophysics",
        "Seismology",
        "Hydrology",
        "Glaciology",
        "Volcanology",
        "Soil science",
        "Earth observation",
        "Biogeochemistry",
    ),
    "Space": (
        "Astrophysics",
        "Cosmology",
        "Planetary science",
        "Astrobiology",
        "Space propulsion",
        "Space life support",
        "Orbital mechanics",
        "Exoplanet science",
        "Gravitational-wave astronomy",
    ),
    "Mathematics": (
        "Graph theory",
        "Topology",
        "Probability theory",
        "Statistics",
        "Number theory",
        "Category theory",
        "Dynamical systems",
        "Game theory",
        "Information theory",
        "Optimisation",
        "Combinatorics",
        "Differential geometry",
        "Numerical analysis",
        "Mathematical logic",
        "Control theory",
    ),
    "Computing and AI": (
        "Machine learning",
        "Deep learning",
        "Reinforcement learning",
        "Natural language processing",
        "Computer vision",
        "AI alignment",
        "Distributed systems",
        "Cryptography",
        "Computer security",
        "Computational complexity",
        "Programming languages",
        "High-performance computing",
        "Neuromorphic computing",
        "Error-correcting codes",
        "Data science",
        "Human-computer interaction",
        "Computer networks",
    ),
    "Engineering": (
        "Robotics",
        "Autonomous systems",
        "Aerospace engineering",
        "Mechanical engineering",
        "Electrical engineering",
        "Civil engineering",
        "Structural engineering",
        "Chemical engineering",
        "Biomedical engineering",
        "Signal processing",
        "Telecommunications",
        "Photonics",
        "Semiconductor devices",
        "Microelectronics",
        "Microfluidics",
        "Additive manufacturing",
        "Advanced manufacturing",
        "Systems engineering",
        "Transport engineering",
        "Water engineering",
    ),
    "Mind and brain": (
        "Cognitive science",
        "Brain-computer interfaces",
        "Computational neuroscience",
        "Cognitive psychology",
        "Decision science",
        "Linguistics",
        "Neuroeconomics",
        "Perception science",
    ),
    "Complex systems": (
        "Complexity science",
        "Network science",
        "Cybernetics",
        "Swarm intelligence",
        "Self-organisation",
        "Collective behaviour",
        "Operations research",
        "Agent-based modelling",
        "Artificial life",
        "Biomimetics",
        "Economics",
        "Behavioural economics",
    ),
    "Food and the living planet": (
        "Agricultural science",
        "Precision agriculture",
        "Food science",
        "Cellular agriculture",
        "Conservation biology",
        "Restoration ecology",
        "Forestry",
        "Fisheries science",
        "Entomology",
        "Biogeography",
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
