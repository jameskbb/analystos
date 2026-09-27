"""Static reference data for the Summit Supply Co. demo company.

Everything here is plain data. The generator turns it into tables with a seeded RNG.
"""

from __future__ import annotations

from dataclasses import dataclass

COMPANY = "Summit Supply Co."


@dataclass(frozen=True)
class Branch:
    branch_id: str
    name: str
    city: str
    state: str
    region: str
    opened: str  # ISO date
    weight: float  # share of the customer base
    sq_ft: int


BRANCHES: tuple[Branch, ...] = (
    Branch("BR-DAL", "Dallas", "Dallas", "TX", "North Texas", "1998-03-02", 0.165, 118_000),
    Branch("BR-FTW", "Fort Worth", "Fort Worth", "TX", "North Texas", "2004-06-14", 0.080, 74_000),
    Branch("BR-PLN", "Plano", "Plano", "TX", "North Texas", "2025-05-01", 0.040, 52_000),
    Branch("BR-HOU", "Houston", "Houston", "TX", "Gulf Coast", "2001-09-10", 0.140, 102_000),
    Branch("BR-CRP", "Corpus Christi", "Corpus Christi", "TX", "Gulf Coast", "2012-04-02", 0.040, 41_000),
    Branch("BR-BPT", "Beaumont", "Beaumont", "TX", "Gulf Coast", "2015-08-17", 0.030, 36_000),
    Branch("BR-AUS", "Austin", "Austin", "TX", "Central Texas", "2006-02-20", 0.100, 83_000),
    Branch("BR-SAT", "San Antonio", "San Antonio", "TX", "Central Texas", "2008-11-03", 0.090, 80_000),
    Branch("BR-WAC", "Waco", "Waco", "TX", "Central Texas", "2016-05-09", 0.030, 33_000),
    Branch("BR-LBB", "Lubbock", "Lubbock", "TX", "West Texas", "2011-07-11", 0.040, 38_000),
    Branch("BR-ELP", "El Paso", "El Paso", "TX", "West Texas", "2010-01-18", 0.050, 45_000),
    Branch("BR-MAF", "Midland", "Midland", "TX", "West Texas", "2018-03-26", 0.030, 31_000),
    Branch("BR-OKC", "Oklahoma City", "Oklahoma City", "OK", "Oklahoma", "2009-10-05", 0.090, 70_000),
    Branch("BR-TUL", "Tulsa", "Tulsa", "OK", "Oklahoma", "2013-04-15", 0.070, 58_000),
)

REGIONS: tuple[str, ...] = ("North Texas", "Gulf Coast", "Central Texas", "West Texas", "Oklahoma")
REGION_INFO = {  # code, regional VP
    "North Texas": ("NTX", "Dana Whitfield"),
    "Gulf Coast": ("GLF", "Marcus Oyelaran"),
    "Central Texas": ("CTX", "Elena Castillo"),
    "West Texas": ("WTX", "Rhett Calloway"),
    "Oklahoma": ("OKL", "Priya Raman"),
}
SEGMENT_INFO = {
    "Enterprise": "Commercial builders and developers on negotiated pricing and net-45 terms",
    "Contractor": "Trade and residential contractors on account, net-30",
    "Retail": "Independent retailers and walk-in trade customers, net-15",
}

SEGMENTS: tuple[str, ...] = ("Enterprise", "Contractor", "Retail")
TIERS: tuple[str, ...] = ("Entry", "Standard", "Premium")
TIER_PRICE_FACTOR = {"Entry": 0.72, "Standard": 1.0, "Premium": 1.55}
TIER_COST_RATIO = {"Entry": 0.745, "Standard": 0.685, "Premium": 0.615}
TIER_BRAND = {"Entry": "ValueBuild", "Standard": "ProLine", "Premium": "Summit Select"}

ORDER_CHANNELS: tuple[str, ...] = ("Counter", "Phone", "Online", "Field Sales")
CHANNEL_MIX = {
    "Enterprise": (0.05, 0.20, 0.30, 0.45),
    "Contractor": (0.40, 0.30, 0.20, 0.10),
    "Retail": (0.60, 0.10, 0.30, 0.00),
}

LEAD_CHANNELS: tuple[str, ...] = ("Paid Search", "Web Form", "Referral", "Trade Show", "Outbound", "Partner")
LEAD_CHANNEL_MIX = (0.36, 0.18, 0.13, 0.09, 0.15, 0.09)
LEAD_CHANNEL_CONVERSION = {
    "Paid Search": 0.30,
    "Web Form": 0.21,
    "Referral": 0.38,
    "Trade Show": 0.24,
    "Outbound": 0.13,
    "Partner": 0.31,
}
LEAD_REGION_MIX = {
    "North Texas": 0.25,
    "Gulf Coast": 0.30,
    "Central Texas": 0.19,
    "West Texas": 0.12,
    "Oklahoma": 0.14,
}


@dataclass(frozen=True)
class Item:
    name: str
    subcategory: str
    uom: str
    base_price: float  # standard-tier list price per uom in Oct 2024
    qty_median: float  # typical contractor line quantity


@dataclass(frozen=True)
class Category:
    name: str
    code: str
    n_products: int
    cost_factor: float  # multiplies the tier cost ratio (commodity categories run thinner margins)
    items: tuple[Item, ...]


CATEGORIES: tuple[Category, ...] = (
    Category(
        "Lumber",
        "LUM",
        64,
        1.06,
        (
            Item("2x4x8 SPF Stud", "Dimensional Lumber", "EA", 4.85, 120),
            Item("2x6x12 SYP", "Dimensional Lumber", "EA", 11.40, 60),
            Item("2x10x16 SYP", "Dimensional Lumber", "EA", 24.90, 30),
            Item("4x4x8 Treated Post", "Treated Lumber", "EA", 13.75, 24),
            Item("7/16 OSB Sheathing 4x8", "Panels", "SHT", 17.20, 60),
            Item("3/4 CDX Plywood 4x8", "Panels", "SHT", 44.50, 30),
            Item("LVL Beam 1-3/4x11-7/8x20", "Engineered Lumber", "EA", 118.00, 6),
            Item("Cedar Fence Picket 5/8x6x6", "Fencing", "EA", 3.95, 150),
        ),
    ),
    Category(
        "Drywall",
        "DRY",
        24,
        1.02,
        (
            Item("1/2 Drywall 4x8", "Gypsum Board", "SHT", 13.90, 80),
            Item("5/8 Type X Drywall 4x12", "Gypsum Board", "SHT", 22.40, 40),
            Item("Joint Compound 4.5 gal", "Finishing", "PAIL", 19.80, 10),
            Item("Paper Joint Tape 500ft", "Finishing", "ROLL", 6.40, 12),
            Item("Metal Corner Bead 8ft", "Finishing", "EA", 3.10, 40),
            Item("Drywall Screws 5lb", "Fasteners", "BOX", 24.50, 6),
        ),
    ),
    Category(
        "Roofing",
        "ROF",
        44,
        1.04,
        (
            Item("Architectural Shingles", "Shingles", "BDL", 38.50, 45),
            Item("3-Tab Shingles", "Shingles", "BDL", 29.90, 45),
            Item("Synthetic Underlayment 10sq", "Underlayment", "ROLL", 96.00, 6),
            Item("Ridge Cap Shingles", "Shingles", "BDL", 64.00, 8),
            Item("Drip Edge 10ft", "Flashing", "EA", 8.60, 30),
            Item("Roofing Nails Coil 7200", "Fasteners", "BOX", 42.00, 4),
            Item("Ice & Water Shield 2sq", "Underlayment", "ROLL", 118.00, 4),
            Item("Ridge Vent 4ft", "Ventilation", "EA", 14.20, 12),
        ),
    ),
    Category(
        "Insulation",
        "INS",
        30,
        0.98,
        (
            Item("R-13 Fiberglass Batts", "Batts", "BAG", 54.00, 14),
            Item("R-30 Fiberglass Batts", "Batts", "BAG", 72.00, 10),
            Item("Blown-In Cellulose 25lb", "Loose Fill", "BAG", 16.80, 30),
            Item("XPS Foam Board 2in 4x8", "Rigid Foam", "SHT", 41.00, 16),
            Item("Closed-Cell Spray Foam Kit", "Spray Foam", "KIT", 465.00, 1),
            Item("Radiant Barrier 500sqft", "Radiant Barrier", "ROLL", 84.00, 3),
        ),
    ),
    Category(
        "Plumbing",
        "PLB",
        58,
        0.96,
        (
            Item("PEX-A Tubing 1/2in 100ft", "Pipe", "ROLL", 58.00, 5),
            Item("PVC Sch40 Pipe 2in 10ft", "Pipe", "EA", 14.20, 20),
            Item("Copper Type L 3/4in 10ft", "Pipe", "EA", 61.00, 6),
            Item("Tank Water Heater 50gal", "Water Heaters", "EA", 780.00, 1),
            Item("Tankless Water Heater", "Water Heaters", "EA", 1450.00, 1),
            Item("Two-Piece Toilet", "Fixtures", "EA", 245.00, 2),
            Item("Kitchen Faucet Pull-Down", "Fixtures", "EA", 189.00, 2),
            Item("Shower Valve Kit", "Fixtures", "EA", 212.00, 2),
            Item("SharkBite Fittings Assortment", "Fittings", "BOX", 38.00, 4),
        ),
    ),
    Category(
        "Electrical",
        "ELC",
        58,
        0.95,
        (
            Item("12/2 NM-B Wire 250ft", "Wire", "ROLL", 128.00, 4),
            Item("14/2 NM-B Wire 250ft", "Wire", "ROLL", 96.00, 4),
            Item("200A Load Center", "Panels", "EA", 265.00, 1),
            Item("Smart Breaker Panel", "Panels", "EA", 1890.00, 1),
            Item("LED Recessed Downlight 6in", "Lighting", "EA", 18.50, 24),
            Item("Tamper-Resistant Receptacle 10pk", "Devices", "PK", 21.00, 6),
            Item("Single-Pole Switch 10pk", "Devices", "PK", 16.50, 6),
            Item("PVC Conduit 3/4in 10ft", "Conduit", "EA", 5.20, 30),
            Item("Smart Thermostat", "Controls", "EA", 229.00, 2),
        ),
    ),
    Category(
        "Tools",
        "TLS",
        44,
        0.92,
        (
            Item("Cordless Drill/Driver Kit", "Power Tools", "EA", 189.00, 1),
            Item("Circular Saw 7-1/4in", "Power Tools", "EA", 159.00, 1),
            Item("Framing Nailer", "Pneumatic", "EA", 329.00, 1),
            Item("Laser Level", "Measuring", "EA", 249.00, 1),
            Item("Cordless Combo Kit 5-Tool", "Power Tools", "EA", 649.00, 1),
            Item("Framing Hammer 22oz", "Hand Tools", "EA", 34.00, 2),
            Item("Tape Measure 25ft", "Measuring", "EA", 21.00, 3),
            Item("Utility Knife Blades 100pk", "Hand Tools", "PK", 17.00, 3),
        ),
    ),
    Category(
        "Hardware",
        "HDW",
        36,
        0.93,
        (
            Item("Structural Screws 50pk", "Fasteners", "BOX", 29.00, 6),
            Item("Joist Hanger 2x10", "Connectors", "EA", 2.60, 60),
            Item("Hurricane Tie", "Connectors", "EA", 1.35, 100),
            Item("Common Nails 16d 50lb", "Fasteners", "BOX", 84.00, 3),
            Item("Door Lever Set", "Door Hardware", "EA", 36.00, 6),
            Item("Deadbolt Single Cylinder", "Door Hardware", "EA", 42.00, 6),
        ),
    ),
    Category(
        "Concrete & Masonry",
        "CON",
        24,
        1.05,
        (
            Item("Concrete Mix 80lb", "Concrete", "BAG", 6.40, 60),
            Item("Mortar Mix 60lb", "Mortar", "BAG", 8.90, 30),
            Item("Rebar #4 20ft", "Reinforcement", "EA", 11.80, 40),
            Item("Concrete Block 8x8x16", "Block", "EA", 2.35, 200),
            Item("Welded Wire Mesh 5x150", "Reinforcement", "ROLL", 189.00, 2),
        ),
    ),
    Category(
        "Paint",
        "PNT",
        18,
        0.90,
        (
            Item("Interior Eggshell 5gal", "Interior Paint", "PAIL", 168.00, 3),
            Item("Exterior Satin 5gal", "Exterior Paint", "PAIL", 196.00, 3),
            Item("Primer 5gal", "Primer", "PAIL", 112.00, 3),
            Item("Caulk Siliconized 10pk", "Sundries", "PK", 38.00, 4),
        ),
    ),
)

# Suppliers, with deliberately inconsistent casing in the raw file (see generator).
SUPPLIERS = {
    "Lumber": ("Piney Woods Timber", "Gulf States Forest Products"),
    "Drywall": ("Lone Prairie Gypsum",),
    "Roofing": ("Red River Roofing Supply", "Caprock Materials"),
    "Insulation": ("Thermacore Insulation", "Blue Norther Building Products"),
    "Plumbing": ("Brazos Plumbing Wholesale", "Riverbend Fixtures"),
    "Electrical": ("Panhandle Electric Supply", "Ampere Distribution"),
    "Tools": ("Ironhide Tool Co.",),
    "Hardware": ("Longhorn Fastener", "Ironhide Tool Co."),
    "Concrete & Masonry": ("Caliche Concrete Products",),
    "Paint": ("Bluebonnet Coatings",),
}

# Line-mix preferences by segment (relative weights by category name).
CATEGORY_MIX = {
    "Enterprise": {
        "Lumber": 0.18,
        "Drywall": 0.09,
        "Roofing": 0.10,
        "Insulation": 0.06,
        "Plumbing": 0.14,
        "Electrical": 0.15,
        "Tools": 0.04,
        "Hardware": 0.10,
        "Concrete & Masonry": 0.10,
        "Paint": 0.04,
    },
    "Contractor": {
        "Lumber": 0.22,
        "Drywall": 0.12,
        "Roofing": 0.14,
        "Insulation": 0.07,
        "Plumbing": 0.10,
        "Electrical": 0.10,
        "Tools": 0.06,
        "Hardware": 0.10,
        "Concrete & Masonry": 0.06,
        "Paint": 0.03,
    },
    "Retail": {
        "Lumber": 0.12,
        "Drywall": 0.06,
        "Roofing": 0.05,
        "Insulation": 0.06,
        "Plumbing": 0.12,
        "Electrical": 0.12,
        "Tools": 0.16,
        "Hardware": 0.17,
        "Concrete & Masonry": 0.05,
        "Paint": 0.09,
    },
}

TIER_MIX = {
    "Enterprise": (0.24, 0.54, 0.22),
    "Contractor": (0.30, 0.52, 0.18),
    "Retail": (0.38, 0.44, 0.18),
}

# Target days of supply by category (inventory policy).
DAYS_OF_SUPPLY = {
    "Lumber": 38,
    "Drywall": 35,
    "Roofing": 45,
    "Insulation": 50,
    "Plumbing": 60,
    "Electrical": 55,
    "Tools": 70,
    "Hardware": 65,
    "Concrete & Masonry": 30,
    "Paint": 55,
}

CALENDAR_SEASONALITY = {
    1: 0.80,
    2: 0.86,
    3: 1.00,
    4: 1.08,
    5: 1.12,
    6: 1.12,
    7: 1.09,
    8: 1.08,
    9: 1.02,
    10: 1.00,
    11: 0.90,
    12: 0.79,
}

NAME_PREFIXES = (
    "Lone Star",
    "Red River",
    "Brazos",
    "Pecos",
    "Bluebonnet",
    "Prairie",
    "Caprock",
    "Llano",
    "Cedar Creek",
    "Post Oak",
    "Longhorn",
    "Hill Country",
    "Gulf Breeze",
    "Big Sky",
    "Mesquite",
    "Canyon",
    "Frontier",
    "Sabine",
    "Palo Duro",
    "Guadalupe",
    "Chisholm",
    "Panhandle",
    "Cimarron",
    "Arbuckle",
    "Wichita",
    "Blackland",
    "Coastal Bend",
    "Twin Oaks",
    "Stonegate",
    "Ironwood",
    "Riverbend",
    "Westfork",
    "Eastgate",
    "High Plains",
    "Crossroads",
    "Heritage",
    "Keystone",
    "Legacy",
    "Northstar",
    "Pinnacle",
    "Redbud",
    "Sandstone",
    "Timberline",
    "Trailhead",
    "Windmill",
    "Oak Hollow",
    "Granite Ridge",
    "Sunbelt",
    "Magnolia",
    "Live Oak",
    "Sierra Blanca",
    "Tejas",
    "Copperline",
    "Bison",
    "Lakeview",
    "Horizon",
    "Saddleback",
    "Cypress",
    "Rio Bravo",
    "Pioneer",
)
TRADE_WORDS = {
    "Enterprise": ("Construction", "Builders", "Development", "Commercial", "Contractors", "Homes"),
    "Contractor": (
        "Builders",
        "Roofing",
        "Remodeling",
        "Framing",
        "Electric",
        "Plumbing",
        "Drywall",
        "Homes",
        "Construction",
        "Restoration",
        "Renovations",
        "Mechanical",
    ),
    "Retail": ("Hardware", "Home Center", "Lumber Yard", "Supply", "Ace Hardware", "Farm & Ranch"),
}
NAME_SUFFIXES = ("LLC", "Inc.", "Co.", "", "", "Group", "LP")

FIRST_NAMES = (
    "James",
    "Maria",
    "Robert",
    "Linda",
    "Michael",
    "Patricia",
    "David",
    "Jennifer",
    "Carlos",
    "Angela",
    "Daniel",
    "Sarah",
    "Luis",
    "Karen",
    "Kevin",
    "Nancy",
    "Brian",
    "Lisa",
    "Jose",
    "Megan",
    "Travis",
    "Amanda",
    "Cody",
    "Brittany",
    "Hector",
    "Rachel",
    "Wade",
    "Jasmine",
    "Dustin",
    "Priya",
    "Tyler",
    "Monica",
    "Garrett",
    "Alicia",
    "Colby",
    "Denise",
    "Ramon",
    "Shelby",
    "Marcus",
    "Yesenia",
)
LAST_NAMES = (
    "Garcia",
    "Johnson",
    "Martinez",
    "Smith",
    "Hernandez",
    "Williams",
    "Lopez",
    "Brown",
    "Nguyen",
    "Davis",
    "Rodriguez",
    "Miller",
    "Wilson",
    "Anderson",
    "Taylor",
    "Thomas",
    "Moore",
    "Jackson",
    "White",
    "Harris",
    "Clark",
    "Lewis",
    "Robinson",
    "Walker",
    "Young",
    "Allen",
    "King",
    "Wright",
    "Scott",
    "Torres",
    "Hill",
    "Green",
    "Adams",
    "Baker",
    "Nelson",
    "Carter",
    "Mitchell",
    "Perez",
)

RETURN_REASONS = (
    "Damaged in transit",
    "Wrong item shipped",
    "Customer over-ordered",
    "Defective",
    "Job cancelled",
    "Pricing dispute",
)

CITY_BY_BRANCH = {
    "BR-DAL": ("Dallas", "Garland", "Irving", "Mesquite", "Richardson"),
    "BR-FTW": ("Fort Worth", "Arlington", "Keller", "Weatherford"),
    "BR-PLN": ("Plano", "Frisco", "McKinney", "Allen"),
    "BR-HOU": ("Houston", "Katy", "Pasadena", "Sugar Land", "The Woodlands"),
    "BR-CRP": ("Corpus Christi", "Portland", "Rockport"),
    "BR-BPT": ("Beaumont", "Port Arthur", "Orange"),
    "BR-AUS": ("Austin", "Round Rock", "Pflugerville", "Georgetown", "Cedar Park"),
    "BR-SAT": ("San Antonio", "New Braunfels", "Boerne", "Schertz"),
    "BR-WAC": ("Waco", "Temple", "Hewitt"),
    "BR-LBB": ("Lubbock", "Levelland", "Wolfforth"),
    "BR-ELP": ("El Paso", "Socorro", "Horizon City"),
    "BR-MAF": ("Midland", "Odessa", "Big Spring"),
    "BR-OKC": ("Oklahoma City", "Edmond", "Norman", "Moore", "Yukon"),
    "BR-TUL": ("Tulsa", "Broken Arrow", "Owasso", "Sand Springs"),
}
