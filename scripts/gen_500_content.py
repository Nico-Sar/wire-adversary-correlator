#!/usr/bin/env python3
"""
scripts/gen_500_content.py
Generate HTML pages and JSON payloads for the 500-URL web server.

Outputs everything to webserver/pages/ for rsync.

Run:  python3 scripts/gen_500_content.py
"""
import json
import os
import random
from datetime import datetime, timedelta, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR   = os.path.join(REPO_ROOT, "webserver", "pages")
os.makedirs(OUT_DIR, exist_ok=True)

RNG = random.Random(2026)

# ── asset pools (already on the web server) ───────────────────────────────
ALL_CSS = [
    "/assets/css/reset.css", "/assets/css/main.css", "/assets/css/typography.css",
    "/assets/css/animations.css", "/assets/css/components.css",
    "/assets/css/dark-mode.css", "/assets/css/forms.css", "/assets/css/grid.css",
]
ALL_JS = [
    "/assets/js/analytics.js", "/assets/js/api-client.js", "/assets/js/app.js",
    "/assets/js/auth.js", "/assets/js/carousel.js",
    "/assets/js/events.js", "/assets/js/search.js",
]
ALL_API = [
    "/assets/api/trending.json", "/assets/api/stats.json",
    "/assets/api/feed.json",      "/assets/api/comments.json",
    "/assets/api/products.json",  "/assets/api/sidebar.json",
    "/assets/api/users.json",     "/assets/api/config.json",
]
ALL_IMG = [f"/assets/img/avatar-0{i}.svg" for i in range(1, 6)] + \
          [f"/assets/img/hero-{i}.svg"    for i in range(1, 6)]

# ── prose-generation helpers ───────────────────────────────────────────────
_OPENERS = [
    "Over the past decade, {k0} has emerged as one of the most consequential developments in {field}.",
    "A landmark study released this week positions {k0} at the forefront of innovation across the {field} sector.",
    "Growing evidence suggests that {k0} will fundamentally reshape how organisations approach {field} challenges.",
    "Industry leaders convening at the annual {field} summit have identified {k0} as the defining priority of the coming decade.",
    "New longitudinal data confirms that sustained investment in {k0} delivers measurable, compounding returns for {field} practitioners.",
    "Analysts tracking the {field} space report that {k0} adoption has accelerated sharply over the past eighteen months.",
    "A cross-institutional review spanning forty countries concludes that {k0} is no longer optional for {field} competitiveness.",
]
_BODY = [
    "The relationship between {k0} and {k1} has long been understood in theory; practical implementations have only recently become viable at scale.",
    "Early adopters report that integrating {k1} alongside {k0} reduces operational overhead by as much as 28 percent while improving downstream outcomes.",
    "A multi-disciplinary team has developed a framework combining {k0} and {k1} to eliminate longstanding bottlenecks that have hindered {field} progress.",
    "The convergence of {k0} and {k1} creates possibilities that neither discipline could achieve independently, opening a rich design space for {field} engineers.",
    "Analysis of over three thousand case studies reveals a consistent pattern: organisations that leverage both {k0} and {k1} outperform sector peers by a significant margin.",
    "Proponents argue that {k1} provides the missing link that makes {k0} deployable in real-world {field} environments rather than controlled laboratory settings.",
    "The economic case is compelling: the combined market for {k0}-enabled {field} solutions is projected to exceed $420 billion by 2032.",
]
_TECHNICAL = [
    "At its core, the approach uses {k1} to model interactions between {k2} and {k3}, producing results that conventional methods cannot replicate.",
    "The architecture treats {k1} and {k2} as complementary rather than competing paradigms, unlocking a new class of {field} system designs.",
    "By combining {k2} with {k3}, researchers have demonstrated a 40-fold improvement in throughput for latency-sensitive {field} workloads.",
    "The key insight is that {k2} and {k3} exhibit emergent synergies under certain boundary conditions—a property long overlooked by mainstream {field} practitioners.",
    "Benchmarks show that a hybrid {k1}/{k2} pipeline outperforms single-paradigm baselines on nine of ten standard {field} evaluation tasks.",
    "Three independent replication attempts at different institutions confirmed the core result, lending unusual confidence to the {k2}–{k3} coupling hypothesis.",
]
_IMPLICATIONS = [
    "The implications for {field} are far-reaching: institutions that delay adoption risk losing ground that may be difficult to recover.",
    "Policy makers have taken note, with several jurisdictions drafting regulatory frameworks that explicitly address {k0} and its role in {field} infrastructure.",
    "Experts caution that while results are promising, the transition from research prototype to {field} production deployment will require sustained cross-sector collaboration.",
    "Sceptics argue that {k0} has been overhyped in previous cycles, but proponents counter that the current evidence base is qualitatively stronger than past claims.",
    "The social dimension cannot be overlooked: equitable access to {k0}-enabled {field} capabilities will require deliberate policy intervention and open standards.",
    "Investors have responded swiftly, with venture capital flowing into {k0}-focused {field} startups at a pace not seen since the mid-2010s platform boom.",
]


def _pick(pool, n, rng):
    chosen = list(rng.sample(pool, min(n, len(pool))))
    return chosen


def _para(field, kws, rng, style="body"):
    pool = {"opener": _OPENERS, "body": _BODY,
            "technical": _TECHNICAL, "implications": _IMPLICATIONS}[style]
    k = (kws + kws)[:4]
    sents = rng.sample(pool, min(3, len(pool)))
    text = " ".join(
        s.format(field=field, k0=k[0], k1=k[1], k2=k[2], k3=k[3])
        for s in sents
    )
    return text


def _section(h2, field, kws, rng):
    p1 = _para(field, kws, rng, style=rng.choice(["opener", "body"]))
    p2 = _para(field, kws, rng, style=rng.choice(["technical", "implications"]))
    return f"<h2>{h2}</h2>\n<p>{p1}</p>\n<p>{p2}</p>"


# ── HTML heavy themes (11–50) ─────────────────────────────────────────────
HEAVY_THEMES = [
    # (title, deck, category, author, field, keywords[6+], section_titles[5], tags[4], date)
    (
        "Neural Scaling Laws Revisited: Why Bigger Models May Have Hit a Wall",
        "A new theoretical framework challenges the assumption that performance gains scale indefinitely with parameter count, reshaping roadmaps at major AI labs.",
        "Artificial Intelligence", "Dr. Priya Sundaram",
        "AI research",
        ["neural scaling", "transformer architecture", "compute budget", "emergent behaviour", "benchmark saturation", "inference efficiency"],
        ["The Scaling Hypothesis Under Scrutiny", "Where the Curve Flattens", "Alternative Paths to Capability", "Industry Roadmap Implications", "Open Questions and Next Steps"],
        ["AI", "Machine Learning", "Deep Learning", "Research"], "2026-01-14",
    ),
    (
        "Arctic Ice Sheet Data Reveals Accelerating Feedback Loops Unseen in Models",
        "Satellite altimetry combined with ocean-floor sensors has detected non-linear melting dynamics that current climate models systematically underestimate.",
        "Climate Science", "Dr. Ingrid Halvorsen",
        "climate science",
        ["ice sheet dynamics", "albedo feedback", "ocean heat flux", "sea level projection", "cryosphere modelling", "climate tipping points"],
        ["Sensor Network Deployment", "Non-Linear Dynamics Detected", "Model Discrepancies Explained", "Sea Level Revision Upward", "Policy Window"],
        ["Climate", "Arctic", "Sea Level", "Feedback Loops"], "2026-01-21",
    ),
    (
        "Artemis IV Crew Returns with 200 kg of Lunar Regolith for In-Situ Resource Tests",
        "The four-person mission completed a record 18-day surface stay, drilling to 3.5 m depth and deploying a permanent seismic network ahead of Gateway construction.",
        "Space Exploration", "Marcus Webb",
        "space exploration",
        ["lunar regolith", "ISRU", "seismic network", "Gateway station", "EVA operations", "Artemis programme"],
        ["Mission Profile", "Record Surface Duration", "Science Return", "ISRU Implications", "What Comes Next"],
        ["NASA", "Moon", "Artemis", "Space"], "2026-02-03",
    ),
    (
        "CRISPR Base-Editing Corrects Sickle-Cell Mutation in 94 % of Treated Patients",
        "A Phase III trial across 28 clinical sites demonstrates durable haematological correction twelve months post-infusion, with no off-target edits detected.",
        "Genomics", "Dr. Amara Diallo",
        "genomics and gene therapy",
        ["base editing", "sickle-cell disease", "haematopoietic stem cells", "off-target effects", "clinical trial", "durable correction"],
        ["Trial Design and Cohort", "Editing Efficiency", "Safety Profile", "Regulatory Pathway", "Access and Cost Challenges"],
        ["CRISPR", "Genomics", "Gene Therapy", "Clinical Trial"], "2026-01-28",
    ),
    (
        "Zero-Knowledge Proofs Reach Production Scale on Ethereum Layer-2 Networks",
        "A new recursive proof system reduces verification latency from 8 seconds to 190 milliseconds, unlocking real-time DeFi applications that were previously impractical.",
        "Blockchain", "Carlos Ferreira",
        "blockchain development",
        ["zero-knowledge proofs", "recursive SNARKs", "layer-2 scaling", "DeFi settlement", "prover hardware", "EVM compatibility"],
        ["The Proof-System Bottleneck", "Recursive Compression Breakthrough", "Latency Benchmarks", "DeFi Integration", "Remaining Challenges"],
        ["Blockchain", "ZK Proofs", "Ethereum", "DeFi"], "2026-02-10",
    ),
    (
        "Basel IV Final Rules Force Banks to Restructure $2.3 Trillion in Capital Buffers",
        "Regulators have published the long-awaited implementation guidance, with the standardised approach now applying to a broader set of credit-risk exposures than banks anticipated.",
        "Financial Regulation", "Sophie Hartmann",
        "financial regulation",
        ["Basel IV", "capital adequacy", "credit risk", "standardised approach", "regulatory capital", "tier-1 ratio"],
        ["Rule Summary", "Capital Impact by Institution Type", "Timeline and Transition", "Competitive Implications", "Industry Response"],
        ["Finance", "Banking", "Regulation", "Basel"], "2026-01-09",
    ),
    (
        "Nation-State Threat Actor Exploits Zero-Day in Border Gateway Protocol Stack",
        "Security researchers have documented a sophisticated campaign targeting critical infrastructure operators across fourteen countries, using a previously unknown BGP parsing flaw.",
        "Cybersecurity", "Lena Fischer",
        "cybersecurity",
        ["BGP vulnerability", "zero-day exploit", "critical infrastructure", "nation-state APT", "network segmentation", "patch deployment"],
        ["Vulnerability Discovery", "Campaign Attribution", "Affected Infrastructure", "Mitigation Guidance", "Systemic Lessons"],
        ["Cybersecurity", "BGP", "APT", "Critical Infrastructure"], "2026-02-17",
    ),
    (
        "Level-4 Robotaxi Fleet Reaches One Billion Commercial Kilometres Without Fatality",
        "The milestone, achieved across three metropolitan markets, provides the strongest statistical evidence yet that autonomous vehicles can outperform human drivers in urban settings.",
        "Autonomous Vehicles", "Raj Patel",
        "autonomous vehicle deployment",
        ["level-4 autonomy", "robotaxi", "safety metrics", "sensor fusion", "edge-case handling", "regulatory approval"],
        ["One Billion Kilometres Context", "Safety Statistics", "System Architecture", "Regulatory Status", "Path to Full Commercial Expansion"],
        ["Autonomous Vehicles", "Safety", "Robotaxi", "AI"], "2026-01-30",
    ),
    (
        "Engineered Microbes Produce Jet Fuel Precursor at Industrial Yield",
        "A synthetic biology team has coaxed E. coli into producing farnesane at 92 g/L titre, a world record that brings bio-jet fuel within striking distance of fossil-fuel cost parity.",
        "Synthetic Biology", "Dr. Yuki Tanaka",
        "synthetic biology",
        ["farnesane biosynthesis", "metabolic engineering", "E. coli chassis", "industrial fermentation", "drop-in fuel", "cost parity"],
        ["Design-Build-Test Cycle", "Titre Achievement", "Techno-Economic Analysis", "Scale-Up Pathway", "Regulatory and Certification Timeline"],
        ["Synthetic Biology", "Biofuel", "Fermentation", "Sustainability"], "2026-02-05",
    ),
    (
        "Humanoid Robots Enter Automotive Assembly Lines at Three Major Manufacturers",
        "Ford, Hyundai, and a European OEM have deployed general-purpose bipedal robots for sub-assembly tasks, marking the first sustained industrial use of humanoid platforms.",
        "Robotics", "Elena Moreau",
        "industrial robotics",
        ["humanoid robot", "bipedal locomotion", "dexterous manipulation", "automotive assembly", "human-robot collaboration", "deployment metrics"],
        ["The Humanoid Bet", "Assembly Task Portfolio", "Performance vs Cobots", "Safety and Liability", "Economics of Humanoid Labour"],
        ["Robotics", "Manufacturing", "Automation", "AI"], "2026-01-18",
    ),
    (
        "15-Minute City Pilots Show 22 % Drop in Peak-Hour Traffic After Two Years",
        "Evaluation data from Paris, Melbourne, and Bogotá confirm that mixed-use zoning combined with dedicated mobility corridors achieves traffic reduction without displacement effects.",
        "Urban Planning", "Prof. Claire Dubois",
        "urban planning",
        ["15-minute city", "mixed-use zoning", "mobility corridor", "traffic reduction", "livability metrics", "gentrification risk"],
        ["Pilot Design", "Traffic and Mobility Outcomes", "Economic Effects on Local Commerce", "Social Equity Findings", "Replication Criteria"],
        ["Urban Planning", "Cities", "Transport", "Sustainability"], "2026-02-12",
    ),
    (
        "Indo-Pacific Trade Framework Adds Six Members, Reshaping Regional Supply Chains",
        "Accession of Vietnam, Indonesia, Bangladesh, Sri Lanka, Cambodia, and the Philippines brings the framework to 19 economies covering 38 % of global goods trade.",
        "Geopolitics", "Ananya Roy",
        "geopolitics and trade",
        ["Indo-Pacific framework", "trade diversification", "supply chain reshoring", "digital trade rules", "labour standards", "customs harmonisation"],
        ["Framework Overview", "New Members and Commitments", "Supply Chain Reorientation", "China's Response", "Outlook"],
        ["Geopolitics", "Trade", "Indo-Pacific", "Supply Chains"], "2026-01-25",
    ),
    (
        "Grid-Scale Iron-Air Batteries Deployed at 200 MWh, Offering 100-Hour Storage",
        "The electrochemical storage system, using abundant iron and oxygen as active materials, achieves a storage cost of $18/kWh installed — far below lithium-ion at this duration.",
        "Renewable Energy", "Dr. Ahmed Hassan",
        "energy storage",
        ["iron-air battery", "long-duration storage", "grid decarbonisation", "levelised cost", "round-trip efficiency", "renewable integration"],
        ["Why Duration Matters", "Iron-Air Chemistry Explained", "Performance Data", "Installed Cost Breakdown", "Grid Integration Challenges"],
        ["Energy Storage", "Renewables", "Grid", "Battery"], "2026-02-19",
    ),
    (
        "Wastewater Surveillance Detects New Pathogen Variant 11 Days Before Clinical Reports",
        "A national environmental monitoring network running AI-assisted sequencing flagged a novel norovirus strain cluster across three cities, enabling pre-emptive supply chain action.",
        "Epidemiology", "Dr. Sarah Kimani",
        "epidemiology and public health",
        ["wastewater surveillance", "environmental sequencing", "early warning", "variant detection", "public health response", "genomic epidemiology"],
        ["Surveillance Network Architecture", "Detection Timeline", "Variant Characterisation", "Public Health Response", "Cost-Benefit of Early Warning"],
        ["Epidemiology", "Public Health", "Genomics", "Wastewater"], "2026-01-07",
    ),
    (
        "Large Language Models Exhibit Consistent Self-Contradictions Across Value Queries",
        "Philosophers and AI safety researchers report that frontier models give mutually inconsistent answers on moral dilemmas when framing is varied, raising questions about alignment.",
        "Philosophy of Mind", "Prof. Tobias Bauer",
        "philosophy of mind and AI",
        ["value alignment", "moral consistency", "LLM introspection", "framing effects", "AI safety", "normative reasoning"],
        ["Experimental Design", "Inconsistency Findings", "Theoretical Interpretations", "Alignment Implications", "The Hard Problem Persists"],
        ["Philosophy", "AI", "Alignment", "Cognition"], "2026-02-08",
    ),
    (
        "Default Opt-Out Saves $4.7 Billion in Wasted Subscription Revenue Annually",
        "A large-scale field study across twelve consumer sectors finds that switching from opt-in to opt-out defaults eliminates 34 % of inertia-driven subscription continuation.",
        "Behavioural Economics", "Prof. Lars Nielsen",
        "behavioural economics",
        ["default bias", "opt-out design", "subscription inertia", "nudge theory", "welfare effects", "consumer protection"],
        ["The Scale of Inertia", "Experimental Evidence", "Welfare Analysis", "Regulatory Response", "Limits of Nudge"],
        ["Behavioural Economics", "Consumer Policy", "Nudge", "Finance"], "2026-01-12",
    ),
    (
        "Roman-Era Harbour Uncovered Under Athens Metro Extension, Rewriting Port History",
        "Construction tunnelling has exposed a 2nd-century trading quay with intact amphora cargo and a customs seal archive, pushing the port's commercial peak two centuries earlier.",
        "Archaeology", "Dr. Nikos Papadopoulos",
        "classical archaeology",
        ["Roman harbour", "amphora archaeology", "customs archive", "underwater excavation", "stratigraphic sequence", "trade network"],
        ["Discovery Circumstances", "Structural Evidence", "Cargo Analysis", "The Customs Seal Archive", "Implications for Mediterranean Trade History"],
        ["Archaeology", "Rome", "Athens", "History"], "2026-02-01",
    ),
    (
        "Cortical Organoids Replicate Sleep-Like Oscillations, Challenging Brain-Body Separation",
        "Lab-grown neural tissue spontaneously generates slow-wave and spindle oscillations that mirror adult human sleep patterns, opening new windows into consciousness research.",
        "Neuroscience", "Dr. Fatima Al-Rashid",
        "neuroscience",
        ["cortical organoid", "sleep oscillations", "slow-wave activity", "consciousness research", "in-vitro model", "neural synchrony"],
        ["Organoid Culture Protocol", "Oscillation Characterisation", "Comparison with In-Vivo Data", "Philosophical Implications", "Ethical Considerations"],
        ["Neuroscience", "Consciousness", "Brain Organoids", "Sleep"], "2026-01-29",
    ),
    (
        "Room-Temperature Superconductor Claim Independently Verified at Three Laboratories",
        "After two previous false positives, a hydrogen-rich clathrate compound at 1.8 GPa has now been confirmed to superconduct at 22 °C by groups in Seoul, Zurich, and Chicago.",
        "Materials Science", "Dr. Min-Jun Park",
        "condensed matter physics",
        ["room-temperature superconductor", "clathrate compound", "Meissner effect", "critical pressure", "independent replication", "materials synthesis"],
        ["Previous False Starts", "Verification Protocol", "Experimental Results", "Pressure Limitation", "Path to Ambient Pressure"],
        ["Materials Science", "Superconductivity", "Physics", "Research"], "2026-02-14",
    ),
    (
        "Post-Quantum TLS 1.4 Draft Finalised, Setting Transition Deadline for 2028",
        "The IETF working group has published the draft standard incorporating CRYSTALS-Kyber and CRYSTALS-Dilithium, with mandatory deprecation of classical key exchange by January 2028.",
        "Quantum Cryptography", "Dr. Hanna Kowalski",
        "cryptography and network security",
        ["post-quantum TLS", "CRYSTALS-Kyber", "key encapsulation", "certificate migration", "IETF standard", "cryptographic agility"],
        ["Why Classical TLS is Threatened", "The New Key Exchange Mechanism", "Certificate and PKI Changes", "Migration Timeline", "Enterprise Readiness"],
        ["Cryptography", "TLS", "Post-Quantum", "Security"], "2026-01-16",
    ),
    (
        "NFT Royalty Enforcement Revived by Smart-Contract Standard ERC-7496",
        "A new token standard embeds royalty logic at the transfer layer, making creator fees unbypassable without marketplace cooperation — reversing three years of enforcement erosion.",
        "Digital Assets", "Jordan Walsh",
        "digital asset markets",
        ["ERC-7496", "NFT royalties", "smart contract", "creator economy", "secondary market", "on-chain enforcement"],
        ["The Royalty Erosion Problem", "How ERC-7496 Works", "Marketplace Adoption", "Creator Revenue Projections", "Legal and Tax Dimensions"],
        ["NFT", "Blockchain", "Creator Economy", "Web3"], "2026-02-22",
    ),
    (
        "Deep-Ocean Polymetallic Nodule Survey Maps 840 000 km² of Untouched Mineral Field",
        "An autonomous underwater vehicle fleet has completed the most detailed benthic survey ever conducted, revealing lithium, cobalt, and manganese concentrations exceeding land-deposit grades.",
        "Ocean Research", "Dr. Celine Bouchard",
        "ocean science",
        ["polymetallic nodules", "benthic survey", "AUV fleet", "critical minerals", "deep-sea mining", "benthic ecology"],
        ["Survey Methodology", "Mineral Resource Estimate", "Ecological Baseline", "Regulatory Landscape", "The Mining Debate"],
        ["Ocean", "Minerals", "AUV", "Environment"], "2026-01-23",
    ),
    (
        "EU Digital Services Act Enforcement Fines Three Platforms a Combined €4.1 Billion",
        "The European Commission's first coordinated action under DSA algorithmic accountability rules has targeted recommendation systems that were found to amplify harmful content.",
        "Digital Policy", "Katrin Müller",
        "platform regulation",
        ["Digital Services Act", "algorithmic accountability", "recommendation systems", "content moderation", "platform liability", "regulatory enforcement"],
        ["DSA Framework Recap", "The Three Investigations", "Algorithmic Audit Findings", "Fines and Remedies", "Global Regulatory Ripple"],
        ["Regulation", "Social Media", "EU", "Algorithms"], "2026-02-06",
    ),
    (
        "AI-Driven Drug Discovery Platform Identifies Novel Alzheimer's Target in 18 Months",
        "A generative protein-structure model screened 14 billion virtual compounds to surface a tau-aggregation inhibitor that has shown neuroprotective effects in three animal models.",
        "Drug Discovery", "Dr. Olusegun Adeyemi",
        "drug discovery and neurology",
        ["generative AI", "protein structure", "tau aggregation", "Alzheimer's target", "virtual screening", "preclinical validation"],
        ["The Target Identification Gap", "Platform Architecture", "Screening Results", "Animal Model Outcomes", "Road to Clinical Trial"],
        ["Drug Discovery", "AI", "Alzheimer's", "Neurology"], "2026-01-31",
    ),
    (
        "Nearshoring Wave Moves $380 Billion in Manufacturing Back to Western Hemisphere",
        "Three years of supply-chain disruption data have catalysed a structural shift, with Mexico, Colombia, and Poland absorbing the largest share of relocated capacity.",
        "Supply Chain", "Maria Gonzalez",
        "supply chain and logistics",
        ["nearshoring", "supply chain resilience", "manufacturing relocation", "just-in-time vs just-in-case", "logistics infrastructure", "labour arbitrage"],
        ["Why Now", "Winning Geographies", "Sector Breakdown", "Infrastructure Bottlenecks", "Long-Term Competitiveness"],
        ["Supply Chain", "Manufacturing", "Trade", "Logistics"], "2026-02-18",
    ),
    (
        "Synthetic Aperture Radar Constellations Enable Daily Monitoring of All Active Volcanoes",
        "With 42 SAR satellites now operational, researchers have for the first time achieved continuous surface-deformation monitoring of all 1 350 potentially active volcanic systems globally.",
        "Satellite Technology", "Dr. Elena Vasquez",
        "remote sensing",
        ["synthetic aperture radar", "volcanic deformation", "InSAR", "early warning", "satellite constellation", "crustal monitoring"],
        ["Why Continuous Monitoring Matters", "Constellation Architecture", "Deformation Detection", "Early Warning Integration", "Case Studies"],
        ["Satellites", "Volcanoes", "Remote Sensing", "Early Warning"], "2026-01-10",
    ),
    (
        "WHO Pandemic Accord Establishes 24-Hour Pathogen-Sharing Treaty with 147 Signatories",
        "After three years of negotiation, the International Pathogen Surveillance Treaty obliges member states to share novel pathogen sequences within 24 hours of detection.",
        "Global Health", "Dr. Ngozi Okonkwo",
        "global health governance",
        ["pandemic accord", "pathogen sharing", "genomic surveillance", "WHO treaty", "equitable access", "outbreak response"],
        ["Treaty Provisions", "Signatories and Hold-Outs", "Surveillance Infrastructure", "Equity Provisions", "Enforcement Mechanisms"],
        ["Public Health", "WHO", "Pandemic Preparedness", "Governance"], "2026-02-20",
    ),
    (
        "Decentralised Identity Standard W3C DID 2.0 Adopted by 28 Governments for e-ID",
        "The specification enables citizens to hold self-sovereign digital credentials on any conformant wallet, with Estonia, Canada, and Singapore among early national deployments.",
        "Digital Identity", "Prof. Mei Lin",
        "digital identity and privacy",
        ["decentralised identity", "W3C DID", "verifiable credential", "self-sovereign identity", "e-ID", "privacy-preserving"],
        ["Why Centralised ID is Failing", "DID 2.0 Architecture", "Government Deployments", "Interoperability Challenges", "Privacy Guarantees"],
        ["Digital Identity", "Privacy", "Government", "Web3"], "2026-01-26",
    ),
    (
        "Small Modular Reactor Fleet Reaches 8 GW Installed Globally, Led by Canada and UK",
        "Twelve commercial SMR units have now achieved grid connection, validating factory-build economics and setting the stage for accelerated deployment through 2035.",
        "Nuclear Energy", "Dr. James O'Brien",
        "nuclear energy",
        ["small modular reactor", "factory construction", "load-following", "carbon-free baseload", "licensing pathway", "waste management"],
        ["SMR Technology Variants", "Construction Cost Actuals", "Grid Integration Role", "Waste and Safety", "Policy Landscape"],
        ["Nuclear", "Energy", "SMR", "Climate"], "2026-02-16",
    ),
    (
        "Singapore's AI-Managed Traffic Grid Reduces Average Commute by 19 Minutes",
        "After 18 months of full-scale deployment, the city-state's real-time signal optimisation system using reinforcement learning has cut average journey times and emissions.",
        "Smart Cities", "Dr. Wei Zhang",
        "smart city technology",
        ["AI traffic management", "reinforcement learning", "signal optimisation", "urban mobility", "emissions reduction", "digital twin"],
        ["System Architecture", "Performance Metrics", "Emissions Impact", "Data Privacy", "Transferability to Other Cities"],
        ["Smart Cities", "AI", "Traffic", "Urban Tech"], "2026-01-20",
    ),
    (
        "LLM-Assisted Compiler Discovers 23 Novel Optimisation Passes, Boosting Throughput 18 %",
        "A code-generating language model trained on LLVM intermediate representation has autonomously identified optimisation sequences that human compiler engineers had not considered.",
        "Compilers", "Dr. Kavya Reddy",
        "compiler technology",
        ["LLVM", "compiler optimisation", "LLM code generation", "intermediate representation", "throughput improvement", "automated reasoning"],
        ["The Compiler Optimisation Search Space", "Model Architecture", "Discovered Passes", "Benchmark Results", "Safety Verification"],
        ["Compilers", "AI", "LLVM", "Performance"], "2026-02-09",
    ),
    (
        "Estonia Exports e-Governance Stack to Fourteen Nations, Generating €900 M in Revenue",
        "The Baltic state's decade-long investment in digital public infrastructure has become a significant export product, with the X-Road data exchange layer now operating in four continents.",
        "E-Governance", "Tiit Kaljurand",
        "digital government",
        ["X-Road", "e-governance", "digital public services", "data sovereignty", "interoperability", "GovTech export"],
        ["Estonia's Digital Journey", "The X-Road Architecture", "Export Countries and Customisation", "Revenue Model", "Risks and Dependencies"],
        ["E-Governance", "Estonia", "Digital Government", "Policy"], "2026-01-08",
    ),
    (
        "Digital CBT Platform Reduces Adolescent Anxiety Scores by 41 % in 8-Week Trial",
        "A randomised controlled trial of a smartphone-delivered cognitive behavioural therapy programme demonstrates efficacy comparable to six in-person sessions.",
        "Mental Health Technology", "Dr. Rachel Stone",
        "mental health and digital therapeutics",
        ["digital CBT", "adolescent anxiety", "RCT", "smartphone therapy", "clinical equivalence", "engagement metrics"],
        ["Trial Design and Recruitment", "Primary Outcome Measures", "Secondary Outcomes", "Drop-Out and Engagement", "Regulatory and Reimbursement Path"],
        ["Mental Health", "Digital Health", "CBT", "Adolescents"], "2026-02-13",
    ),
    (
        "Hyperspectral Drones Reduce Nitrogen Fertiliser Use by 31 % Across 50 000 ha",
        "A large-scale European precision agriculture trial shows that per-plant nutrient prescription driven by aerial hyperspectral imaging matches yield while cutting input costs significantly.",
        "Precision Agriculture", "Dr. Hans Weber",
        "precision agriculture",
        ["hyperspectral imaging", "variable-rate application", "nitrogen optimisation", "drone sensing", "yield mapping", "input efficiency"],
        ["Why Blanket Application Fails", "Drone Fleet and Sensing", "Prescription Algorithm", "Yield and Input Outcomes", "Adoption Barriers"],
        ["Agriculture", "Drones", "Precision Farming", "Sustainability"], "2026-01-17",
    ),
    (
        "Apple Vision Pro 3 Achieves Retinal-Resolution Display, Enabling MR Surgery Guidance",
        "The third-generation mixed-reality headset introduces a 12K-per-eye μLED panel and sub-millisecond tracking, clearing the bar for clinical use in minimally invasive procedures.",
        "Augmented Reality", "Samira Osei",
        "mixed reality technology",
        ["retinal-resolution display", "μLED", "mixed reality", "surgical guidance", "eye tracking", "clinical AR"],
        ["Display Technology Leap", "Tracking and Latency", "Surgical Use-Case Trials", "Regulatory Clearance", "Consumer vs Clinical Markets"],
        ["AR", "MR", "Healthcare", "Display Technology"], "2026-02-07",
    ),
    (
        "Hadal Zone Expedition Recovers Plastic Microparticles at 10 924 m Depth",
        "A full-ocean-depth expedition to the Challenger Deep has documented microplastic contamination in benthic sediments, trench water, and organisms at every sampled depth.",
        "Deep Sea Research", "Dr. Akira Nakamura",
        "deep sea oceanography",
        ["hadal zone", "microplastics", "Challenger Deep", "benthic contamination", "full-ocean-depth sampling", "plastic transport mechanisms"],
        ["Expedition Logistics", "Sampling Methodology", "Contamination Findings", "Transport Pathway Analysis", "Ecological Implications"],
        ["Oceans", "Microplastics", "Deep Sea", "Environment"], "2026-01-22",
    ),
    (
        "mRNA Platform Produces Universal Influenza Vaccine Showing Broad-Strain Protection",
        "A mosaic antigen design targeting conserved haemagglutinin stalk regions has produced 87 % protection against H1, H3, and B lineages in a Phase II trial.",
        "Virology", "Dr. Laura Bianchi",
        "virology and vaccine development",
        ["mRNA vaccine", "universal influenza", "haemagglutinin stalk", "mosaic antigen", "cross-strain protection", "Phase II trial"],
        ["Why Strain-Specific Vaccines Fall Short", "Mosaic Antigen Design", "Phase II Results", "Manufacturing Scalability", "Regulatory Pathway"],
        ["Virology", "Vaccines", "mRNA", "Influenza"], "2026-02-04",
    ),
    (
        "Commonwealth Fusion SPARC Reactor Achieves Q>1 Plasma Condition for 7 Seconds",
        "The compact high-field tokamak has demonstrated a sustained burning plasma where fusion energy output exceeds external heating input, a first for a privately funded programme.",
        "Fusion Energy", "Dr. Oliver Grant",
        "fusion energy research",
        ["tokamak", "high-temperature superconducting magnets", "burning plasma", "Q factor", "SPARC reactor", "net energy gain"],
        ["The Q>1 Threshold Explained", "Experimental Conditions", "HTS Magnet Performance", "Path to ARC Power Plant", "Timeline and Funding"],
        ["Fusion", "Energy", "Physics", "Climate"], "2026-01-05",
    ),
    (
        "EU Carbon Border Adjustment Mechanism Adds Steel, Cement, and Hydrogen from 2027",
        "An expansion of the CBAM scope, confirmed by the European Parliament, will apply the carbon price to three additional sectors, affecting imports from 72 trade partners.",
        "Carbon Markets", "Ingrid Larsson",
        "carbon pricing and trade",
        ["CBAM", "carbon border adjustment", "embedded emissions", "steel decarbonisation", "trade competitiveness", "EU ETS"],
        ["CBAM Recap", "Expanded Sector Coverage", "Impact on Major Exporters", "Domestic Industry Response", "WTO Compatibility"],
        ["Carbon Markets", "EU Policy", "Trade", "Climate"], "2026-02-24",
    ),
    (
        "AI Contract Analysis Reduces Due Diligence Time by 73 % at Ten Major Law Firms",
        "A controlled deployment of large-language-model contract review tools across ten firms shows dramatic time savings with error rates below senior associate benchmarks.",
        "Legal Technology", "Alexandra Chen",
        "legal technology",
        ["AI contract review", "due diligence", "LLM legal", "error rate benchmarking", "law firm adoption", "regulatory risk"],
        ["The Due Diligence Bottleneck", "Tool Architecture and Training", "Performance vs Human Baseline", "Error Analysis", "Liability and Regulatory Questions"],
        ["Legal Tech", "AI", "Law", "Productivity"], "2026-01-15",
    ),
]

# ── News themes (6–20) ────────────────────────────────────────────────────
NEWS_THEMES = [
    ("Landslide Election Result Reshapes Ruling Coalition in Historic Vote",
     "The incumbent party secures a reduced majority as opposition gains push the legislature toward a historic power-sharing arrangement not seen in forty years.",
     "Politics", "Thomas Blair", "2026-01-03",
     ["political", "coalition", "majority", "election", "legislature", "party"], ["Election Night Results", "Coalition Maths", "Policy Implications", "International Reactions", "What Happens Next"]),
    ("Championship Decider: Underdogs Win World Cup Final in Penalty Shootout",
     "A 120-minute goalless draw gives way to a dramatic spot-kick finale, with the tournament's lowest-ranked qualifier claiming the trophy for the first time in the nation's history.",
     "Sports", "Chris Hadfield", "2026-01-11",
     ["football", "championship", "penalty", "shootout", "tournament", "underdogs"], ["Match Report", "Tactical Analysis", "Player Ratings", "Iconic Moments", "What the Win Means"]),
    ("Global Equity Markets Shed $4 Trillion in Worst Single-Day Drop Since 2020",
     "Simultaneous credit-rating downgrades across three G7 economies triggered a cascade of algorithmic sell orders that circuit breakers struggled to contain.",
     "Finance", "Diana Moss", "2026-01-19",
     ["equity markets", "credit rating", "sell-off", "circuit breaker", "volatility", "central bank"], ["Market Chronology", "Trigger Analysis", "Central Bank Response", "Sector Breakdown", "Outlook"]),
    ("Outbreak Declared: Haemorrhagic Fever Spreads to Seven Cities Across Two Continents",
     "Health authorities have activated emergency protocols after confirmed human-to-human transmission pushes the fatality count above 200, prompting travel advisories from 30 countries.",
     "Health", "Dr. Ama Owusu", "2026-01-27",
     ["outbreak", "haemorrhagic fever", "epidemic", "WHO", "contact tracing", "quarantine"], ["Epidemiological Situation", "Clinical Profile", "Response Measures", "International Coordination", "Vaccine Prospects"]),
    ("Reusable Super-Heavy Rocket Completes First Crewed Lunar Flyby",
     "The six-person crew completed two orbits of the Moon before a nominal re-entry and ocean splashdown, validating life-support systems ahead of the surface-landing mission.",
     "Space", "Marcus Webb", "2026-02-02",
     ["crewed spaceflight", "lunar flyby", "reusable rocket", "life support", "splashdown", "NASA"], ["Mission Timeline", "Crew Experience", "Technical Performance", "Lunar Photography", "Next Mission"]),
    ("Ceasefire Agreement Signed After 18 Months of Regional Conflict",
     "Mediated by a neutral third party, the truce agreement establishes a demilitarised buffer zone and commits both sides to internationally monitored elections within 18 months.",
     "World", "Farida Khalil", "2026-02-11",
     ["ceasefire", "peace agreement", "conflict", "mediation", "election", "humanitarian"], ["Agreement Terms", "Humanitarian Situation", "International Monitors", "Reaction from Both Sides", "Fragility Risks"]),
    ("Tech Giant Raises $18 Billion in Largest Software IPO of the Decade",
     "The cloud-infrastructure company priced at the top of its range and opened 47 % higher, drawing comparisons to landmark listings of the 2010s and reigniting IPO market optimism.",
     "Technology", "Jordan Walsh", "2026-02-23",
     ["IPO", "tech listing", "cloud infrastructure", "stock market", "valuation", "venture capital"], ["IPO Terms", "First-Day Trading", "Company Financials", "Comparisons to Peers", "What Comes Next"]),
    ("Global Climate Summit Produces Binding 1.5°C Commitment from 145 Nations",
     "After three weeks of negotiations in Nairobi, delegations have endorsed a legally binding protocol that requires net-zero electricity grids in all signatory nations by 2035.",
     "Environment", "Ingrid Larsson", "2026-01-06",
     ["climate summit", "1.5 degrees", "net zero", "binding agreement", "renewable energy", "carbon budget"], ["Summit Outcome", "Key Commitments", "Finance Mechanism", "Who Signed", "Implementation Challenges"]),
    ("Supreme Court Ruling Overturns Data-Localisation Mandate for Cloud Providers",
     "A 6-3 decision holds that requiring foreign cloud operators to store data exclusively within national borders violates existing trade treaty obligations.",
     "Law", "Alexandra Chen", "2026-02-15",
     ["Supreme Court", "data localisation", "cloud computing", "trade law", "privacy", "ruling"], ["Case Background", "Majority Opinion", "Dissents", "Industry Reaction", "Regulatory Aftermath"]),
    ("$500 Billion National Infrastructure Fund Launches with Green-First Mandate",
     "The sovereign wealth-backed programme prioritises flood defences, EV charging networks, and rail electrification, with an independent board overseeing project selection.",
     "Infrastructure", "Raj Patel", "2026-01-24",
     ["infrastructure", "sovereign fund", "EV charging", "rail electrification", "flood defence", "green bond"], ["Fund Structure", "Priority Projects", "Governance", "Economic Multiplier Estimates", "Opposition Critique"]),
    ("Historic Diplomatic Handshake Ends 60-Year Territorial Dispute",
     "The two neighbouring nations have signed a maritime delimitation treaty, unlocking joint development of an offshore energy field estimated at 1.2 trillion cubic feet of gas.",
     "Diplomacy", "Ananya Roy", "2026-02-25",
     ["diplomacy", "maritime boundary", "territorial dispute", "offshore energy", "treaty", "joint development"], ["The Dispute History", "Treaty Terms", "Energy Resources", "Regional Reactions", "Implementation Timeline"]),
    ("Astronomers Detect Repeating Radio Signal from Habitable-Zone Exoplanet",
     "A narrowband signal repeating on a 73-minute cycle has passed initial radio frequency interference screening, prompting a coordinated multi-observatory follow-up campaign.",
     "Science", "Dr. Priya Sundaram", "2026-01-13",
     ["radio signal", "exoplanet", "SETI", "habitable zone", "narrowband", "observatory"], ["Signal Characteristics", "RFI Screening", "Host Star and Planet", "SETI Protocol", "Expert Reactions"]),
    ("Palme d'Or Winner Becomes Fastest Film to Reach 200 Million Streaming Views",
     "The critically acclaimed drama, shot in three languages across five countries, set a record 72 hours after its simultaneous theatrical and streaming release.",
     "Culture", "Elena Moreau", "2026-02-21",
     ["Palme d'Or", "streaming", "film", "cinema", "record", "distribution"], ["Film Synopsis", "Awards Journey", "Streaming Strategy", "Cultural Impact", "Director Interview"]),
    ("Central Bank Surprises Markets with 75 bps Emergency Rate Cut",
     "Citing deteriorating leading indicators and tightening credit conditions, the monetary authority moved outside its scheduled calendar in the first emergency cut in six years.",
     "Economics", "Sophie Hartmann", "2026-01-04",
     ["interest rate", "central bank", "emergency cut", "monetary policy", "credit conditions", "recession risk"], ["Decision Announcement", "Economic Context", "Market Reaction", "Dissenting Views", "Outlook"]),
    ("Million-Person March Demands Electoral Reform in Capital",
     "Protesters have occupied the central boulevard for a fourth consecutive day, presenting a formal petition of twelve constitutional amendments to the speaker of parliament.",
     "Politics", "Thomas Blair", "2026-02-28",
     ["protest", "electoral reform", "democracy", "petition", "parliament", "civil society"], ["Protest Origins", "Key Demands", "Government Response", "Crowd Profile", "Historical Parallels"]),
]

# ── Shop themes (6–20) ────────────────────────────────────────────────────
SHOP_THEMES = [
    ("Home Electronics", "Smart TVs, soundbars, and streaming devices", "Electronics & TV",
     ["4K display", "OLED", "soundbar", "streaming", "smart home"], 8),
    ("Outdoor Adventure Gear", "Tents, hiking boots, and trail nutrition", "Outdoor & Camping",
     ["tent", "hiking", "trail", "backpack", "waterproof"], 10),
    ("Luxury Watches", "Swiss mechanical and smart hybrid timepieces", "Watches & Jewellery",
     ["mechanical", "sapphire crystal", "Swiss movement", "chronograph", "bezel"], 6),
    ("Home Appliances", "Refrigerators, washers, and dishwashers", "Home Appliances",
     ["energy rating", "capacity", "inverter motor", "smart connectivity", "warranty"], 12),
    ("Office Supplies", "Ergonomic furniture, stationery, and printing", "Office & Stationery",
     ["ergonomic", "standing desk", "printer", "monitor arm", "cable management"], 8),
    ("Pet Accessories", "Collars, beds, and nutrition for cats and dogs", "Pets",
     ["harness", "orthopedic bed", "grain-free", "microchip", "enzymatic cleaner"], 9),
    ("Baby & Toddler", "Strollers, car seats, and nursery essentials", "Baby Products",
     ["safety rating", "stroller", "car seat", "BPA-free", "baby monitor"], 7),
    ("Art & Craft Supplies", "Paints, canvases, and sculpting materials", "Arts & Crafts",
     ["acrylic paint", "canvas", "sculpting clay", "easel", "brush set"], 11),
    ("Sports & Fitness", "Dumbbells, yoga mats, and cycling gear", "Sports Equipment",
     ["resistance", "yoga mat", "dumbbell", "cycling computer", "heart rate monitor"], 10),
    ("Books & Media", "Bestsellers, textbooks, and audiobooks", "Books & Media",
     ["bestseller", "hardcover", "audiobook", "e-reader", "academic"], 8),
    ("Automotive Parts", "Tyres, batteries, and performance accessories", "Automotive",
     ["tyre rating", "battery capacity", "OEM fit", "torque spec", "fitment guide"], 6),
    ("Garden & Outdoors", "Lawnmowers, planters, and irrigation", "Garden",
     ["cordless", "irrigation", "raised bed", "composting", "solar lighting"], 9),
    ("Toys & Games", "Board games, STEM kits, and outdoor play", "Toys & Games",
     ["age-appropriate", "STEM", "board game", "educational", "BPA-free"], 12),
    ("Beauty & Cosmetics", "Skincare serums, lipsticks, and fragrances", "Beauty",
     ["SPF", "hyaluronic acid", "cruelty-free", "fragrance", "retinol serum"], 8),
    ("Gourmet Food & Drink", "Artisan coffee, specialty oils, and gift hampers", "Food & Drink",
     ["single-origin", "cold-pressed", "award-winning", "artisan", "gift hamper"], 7),
]

# ── Social themes (6–20) ─────────────────────────────────────────────────
SOCIAL_THEMES = [
    ("Travel Photography", "Sunrise over Cappadocia hot-air balloons",
     "travel", ["travel", "landscape", "photography", "sunrise", "explore"]),
    ("Fitness & Wellness", "Six-week transformation progress update",
     "fitness", ["workout", "progress", "strength", "nutrition", "personal best"]),
    ("Home Cooking", "Sourdough starter day 7 — open crumb achieved",
     "food", ["sourdough", "fermentation", "baking", "crumb", "recipe"]),
    ("Music Discovery", "Five albums that rewired my brain this month",
     "music", ["album", "playlist", "indie", "artist", "listen"]),
    ("Gaming Highlights", "Finally hit Grandmaster in Season 14",
     "gaming", ["rank", "esports", "highlight", "team", "strategy"]),
    ("Book Club", "This month's pick — spoiler-free review inside",
     "books", ["novel", "plot", "characters", "literary fiction", "recommend"]),
    ("Wildlife Photography", "Golden hour with a family of wild elephants",
     "nature", ["wildlife", "telephoto", "safari", "conservation", "golden hour"]),
    ("Startup Founders", "What I wish I knew before raising a seed round",
     "startup", ["fundraising", "pitch deck", "investors", "term sheet", "founder"]),
    ("Cooking Challenge", "Street-food recreations from 12 countries in 12 days",
     "food", ["street food", "technique", "spice", "authentic", "challenge"]),
    ("Tech Commentary", "Hot take: the smartphone plateau is actually good",
     "tech", ["smartphone", "innovation", "market", "opinion", "upgrade cycle"]),
    ("Political Discussion", "Thread: why ranked-choice voting changes strategy",
     "politics", ["voting", "ranked choice", "strategy", "policy", "democracy"]),
    ("Comedy Clips", "Funniest moments from last night's stand-up special",
     "comedy", ["stand-up", "punchline", "timing", "special", "laughs"]),
    ("Language Learning", "30-day Mandarin challenge — week 4 update",
     "language", ["Mandarin", "tones", "characters", "immersion", "progress"]),
    ("Dog & Pet Community", "My rescue greyhound's first year at home",
     "pets", ["rescue", "greyhound", "adoption", "training", "bonding"]),
    ("Architecture & Design", "Brutalist buildings you need to see before they disappear",
     "design", ["brutalism", "concrete", "architecture", "heritage", "urban"]),
]

# ── Forum themes (6–20) ──────────────────────────────────────────────────
FORUM_THEMES = [
    ("Python debugging", "Strange asyncio timeout only in production — help",
     "Python", "asyncio", ["async", "timeout", "event loop", "debug", "production"]),
    ("PC hardware builds", "£1500 build for 4K gaming and light video editing",
     "Hardware", "build", ["GPU", "CPU", "RAM", "NVMe", "cooling"]),
    ("Crypto trading", "Backtesting a mean-reversion strategy on BTC 15m",
     "Trading", "strategy", ["backtest", "mean reversion", "Sharpe ratio", "slippage", "bitcoin"]),
    ("Linux sysadmin", "Setting up WireGuard on a multi-homed server",
     "Linux", "networking", ["WireGuard", "routing table", "NAT", "systemd", "firewall"]),
    ("Game modding", "Skyrim script extender breaking after patch 1.6.1170",
     "Modding", "Skyrim", ["SKSE", "mod list", "load order", "papyrus", "plugin"]),
    ("Homelab", "Migrating bare metal to Proxmox — storage planning question",
     "Homelab", "virtualisation", ["Proxmox", "ZFS", "Ceph", "VLAN", "backup"]),
    ("Security CTF", "[writeup] Buffer overflow on heap — ASIS CTF 2026",
     "CTF", "exploit", ["heap overflow", "ASLR bypass", "ROP chain", "libc leak", "shellcode"]),
    ("Amateur radio", "FT8 DX from EU to VK on 10m — conditions report",
     "Radio", "propagation", ["FT8", "DX", "propagation", "ionosphere", "WSJT-X"]),
    ("3D printing", "Warping on large PETG prints — slicer settings share",
     "3D Printing", "PETG", ["warping", "bed adhesion", "enclosure", "slicer", "filament"]),
    ("Data science", "Confusion on target leakage in a time-series pipeline",
     "Data Science", "ML", ["target leakage", "time series", "cross-validation", "feature engineering", "pipeline"]),
    ("Astronomy", "First light report — 12-inch Dobsonian from Bortle 4",
     "Astronomy", "observations", ["Dobsonian", "eyepiece", "collimation", "deep sky", "seeing"]),
    ("Recipe exchange", "Achieving perfect crust on cast-iron sourdough",
     "Cooking", "sourdough", ["cast iron", "steam", "scoring", "fermentation", "oven spring"]),
    ("Board games", "Ranking every Deckbuilder released in 2025",
     "Games", "deckbuilder", ["deckbuilder", "engine building", "tableau", "shuffle", "combo"]),
    ("Motorcycles", "Tyre choice for daily commuting + weekend canyon runs",
     "Motorcycles", "tyres", ["sport touring", "wet grip", "tread life", "compound", "profile"]),
    ("Personal finance", "Portfolio review — 30-year-old with 15 % savings rate",
     "Finance", "investing", ["index fund", "allocation", "rebalancing", "tax wrapper", "compound"]),
]


# ── HTML rendering ─────────────────────────────────────────────────────────
def _sub_resources(n_css, n_js, n_img, n_api, rng):
    css  = _pick(ALL_CSS, n_css, rng)
    js   = _pick(ALL_JS,  n_js,  rng)
    imgs = _pick(ALL_IMG, n_img, rng)
    api  = _pick(ALL_API, n_api, rng)
    return css, js, imgs, api


def _css_links(css):
    return "\n".join(f'  <link rel="stylesheet" href="{c}">' for c in css)


def _js_tags(js):
    return "\n".join(f'  <script src="{s}" defer></script>' for s in js)


def _fetch_script(api_urls, widget_id="sidebar-widget"):
    calls = []
    for url in api_urls:
        key = url.split("/")[-1].replace(".json", "")
        calls.append(
            f"    fetch('{url}').then(function(r){{return r.json();}}).then(function(d){{"
            f"var w=document.getElementById('{key}-slot');if(w)w.setAttribute('data-loaded','1');}}).catch(function(){{}});"
        )
    return (
        "<script>\n(function(){\n"
        + "\n".join(calls)
        + "\n})();\n</script>"
    )


def render_heavy_page(theme_tuple, page_num, rng):
    (title, deck, category, author, field, kws, sections, tags, date) = theme_tuple
    n_css = rng.randint(3, 4)
    n_js  = rng.randint(3, 4)
    n_img = rng.randint(1, 2)
    n_api = rng.randint(2, 3)
    css, js, imgs, api = _sub_resources(n_css, n_js, n_img, n_api, rng)
    total_resources = n_css + n_js + n_img + n_api

    css_block = _css_links(css)
    js_block  = _js_tags(js)

    section_html = ""
    for i, sec_title in enumerate(sections):
        k_offset = i % max(1, len(kws) - 3)
        kw_slice = (kws[k_offset:] + kws)[:4]
        p1 = _para(field, kw_slice, rng, style=rng.choice(["opener", "body"]))
        p2 = _para(field, kw_slice, rng, style=rng.choice(["technical", "implications"]))
        section_html += f"\n        <h2>{sec_title}</h2>\n        <p>{p1}</p>\n        <p>{p2}</p>\n"

    # Key findings bullets
    findings = []
    for _ in range(5):
        kw_slice = (kws + kws)[:4]
        sent = rng.choice(_BODY + _TECHNICAL)
        findings.append(sent.format(field=field, k0=kws[0], k1=kws[1 % len(kws)],
                                    k2=kws[2 % len(kws)], k3=kws[3 % len(kws)]))
    findings_html = "\n".join(f"          <li>{f}</li>" for f in findings)

    # Related articles
    related_titles = [
        f"The Future of {kws[0].title()} in {field.title()}",
        f"How {kws[1].title()} Is Changing {category} Forever",
        f"A Deep Dive Into {kws[2 % len(kws)].title()} Research",
    ]
    related_html = ""
    for rt in related_titles:
        desc = _para(field, kws, rng, style="body")[:180] + "..."
        related_html += f'<div class="related-item"><h4>{rt}</h4><p>{desc}</p></div>\n'

    img_tags = ""
    for src in imgs:
        img_tags += f'<img src="{src}" alt="Article illustration" loading="lazy" width="120" height="80" class="inline-img">\n'

    api_slots = "".join(
        f'<span id="{u.split("/")[-1].replace(".json","")}-slot"></span>' for u in api
    )

    tag_html = " ".join(f'<span class="tag">{t}</span>' for t in tags)
    fetch_js = _fetch_script(api)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
  <meta http-equiv="Pragma" content="no-cache">
  <meta http-equiv="Expires" content="0">
  <title>{title}</title>
  <!-- {total_resources} sub-resources: {n_css} CSS · {n_js} JS · {n_img} img · {n_api} API -->
{css_block}
{js_block}
</head>
<body>
  <header class="site-header">
    <nav class="primary-nav">
      <a href="/">Home</a>
      <a href="/technology">Technology</a>
      <a href="/science">Science</a>
      <a href="/business">Business</a>
      <a href="/opinion">Opinion</a>
      <a href="/video">Video</a>
    </nav>
    <div class="breaking-banner">LATEST: {deck[:80]}</div>
  </header>

  <main class="article-layout">
    <article class="lead-article">
      <header class="article-header">
        <div class="article-meta">
          <span class="category-tag">{category}</span>
          <span class="dot-separator">·</span>
          <time datetime="{date}">{date}</time>
          <span class="dot-separator">·</span>
          <span class="read-time">10 min read</span>
        </div>
        <h1 class="headline">{title}</h1>
        <p class="deck">{deck}</p>
        <div class="byline">
          {img_tags}
          <div class="byline-text">
            <span class="author-name">{author}</span>
            <span class="author-role">Senior Correspondent, {category}</span>
          </div>
        </div>
      </header>

      <div class="article-body">
        {section_html}

        <section class="key-findings">
          <h3>Key Findings</h3>
          <ul>
{findings_html}
          </ul>
        </section>

        <section class="related-reading">
          <h3>Related Reading</h3>
          {related_html}
        </section>
      </div>

      <footer class="article-footer">
        <div class="tags-row">{tag_html}</div>
        <div class="share-row">
          <button type="button" class="share-btn">Share</button>
          <button type="button" class="save-btn">Save</button>
        </div>
      </footer>
    </article>

    <aside class="sidebar">
      <section class="trending-module">
        <h3>Trending Now</h3>
        <ol id="trending-list"><li>Loading…</li></ol>
      </section>
      <section class="newsletter-module">
        <h3>{category} Newsletter</h3>
        <p>The most important stories in {category.lower()}, explained.</p>
        <form action="#" method="post" class="newsletter-form">
          <input type="email" placeholder="your@email.com" name="email" required>
          <button type="submit">Subscribe</button>
        </form>
      </section>
      <div id="stats-widget">{api_slots}</div>
    </aside>
  </main>

  <footer class="site-footer">
    <p>Traffic-analysis research page &mdash; page_html_heavy_{page_num} &mdash; {total_resources} sub-resources</p>
  </footer>
  {fetch_js}
</body>
</html>"""


def render_news_page(theme_tuple, page_num, rng):
    (title, deck, category, author, date, kws, sections) = theme_tuple
    n_css, n_js, n_img, n_api = 2, 2, 1, 2
    css, js, imgs, api = _sub_resources(n_css, n_js, n_img, n_api, rng)
    total_resources = n_css + n_js + n_img + n_api

    section_html = ""
    field = category.lower()
    for sec_title in sections:
        p1 = _para(field, kws, rng, style="body")
        p2 = _para(field, kws, rng, style="implications")
        section_html += f"\n        <h2>{sec_title}</h2>\n        <p>{p1}</p>\n        <p>{p2}</p>\n"

    img_tag = f'<img src="{imgs[0]}" alt="Article image" loading="eager" width="800" height="420" class="hero-image">' if imgs else ""
    tag_html = " ".join(f'<span class="tag">{k}</span>' for k in kws[:4])
    fetch_js = _fetch_script(api)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
  <meta http-equiv="Pragma" content="no-cache">
  <meta http-equiv="Expires" content="0">
  <title>{title}</title>
  <!-- {total_resources} sub-resources -->
{_css_links(css)}
{_js_tags(js)}
</head>
<body>
  <header class="site-header">
    <nav class="primary-nav">
      <a href="/">Home</a><a href="/news">News</a><a href="/world">World</a>
      <a href="/business">Business</a><a href="/tech">Tech</a>
    </nav>
  </header>
  <main class="article-layout">
    <article>
      <div class="article-meta">
        <span class="category-tag">{category}</span>
        <time datetime="{date}">{date}</time>
      </div>
      <h1>{title}</h1>
      <p class="deck">{deck}</p>
      <p class="byline">By <strong>{author}</strong></p>
      <figure>{img_tag}</figure>
      <div class="article-body">{section_html}</div>
      <div class="tags-row">{tag_html}</div>
    </article>
    <aside class="sidebar">
      <h3>More {category}</h3>
      <ul id="feed-list"><li>Loading…</li></ul>
    </aside>
  </main>
  <footer class="site-footer">
    <p>page_news_{page_num} &mdash; {total_resources} sub-resources</p>
  </footer>
  {fetch_js}
</body>
</html>"""


def render_shop_page(theme_tuple, page_num, rng):
    (section_name, tagline, category, kws, n_products) = theme_tuple
    n_css, n_js, n_img, n_api = 2, 2, 2, 2
    css, js, imgs, api = _sub_resources(n_css, n_js, n_img, n_api, rng)
    total_resources = n_css + n_js + n_img + n_api

    products_html = ""
    for i in range(n_products):
        kw = kws[i % len(kws)]
        price = rng.randint(15, 899)
        rating = round(rng.uniform(3.5, 5.0), 1)
        img_src = ALL_IMG[i % len(ALL_IMG)]
        products_html += f"""
        <div class="product-card">
          <img src="{img_src}" alt="{kw} product" loading="lazy" width="240" height="240">
          <h3 class="product-title">Premium {kw.title()} — {section_name} Edition</h3>
          <p class="product-desc">High-quality {kw} for {category.lower()} enthusiasts. Rated {rating}/5 by verified buyers.</p>
          <p class="product-price">${price}.00</p>
          <button class="add-to-cart">Add to Cart</button>
        </div>"""

    fetch_js = _fetch_script(api)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
  <meta http-equiv="Pragma" content="no-cache">
  <meta http-equiv="Expires" content="0">
  <title>{section_name} — Shop</title>
  <!-- {total_resources} sub-resources -->
{_css_links(css)}
{_js_tags(js)}
</head>
<body>
  <header class="shop-header">
    <nav><a href="/">Home</a> › <a href="/shop">{category}</a> › {section_name}</nav>
    <div class="shop-banner">{tagline}</div>
  </header>
  <main>
    <h1>{section_name}</h1>
    <p class="category-desc">Browse our curated selection of {section_name.lower()}. {tagline}</p>
    <div class="filter-bar">
      <button>Sort: Bestselling</button>
      <button>Filter: In Stock</button>
      <select><option>Price: Any</option><option>Under $50</option><option>$50–$200</option><option>$200+</option></select>
    </div>
    <div class="product-grid">{products_html}</div>
  </main>
  <footer class="site-footer">
    <p>page_shop_{page_num} &mdash; {total_resources} sub-resources</p>
  </footer>
  {fetch_js}
</body>
</html>"""


def render_social_page(theme_tuple, page_num, rng):
    (title, subtitle, tag, kws) = theme_tuple[0], theme_tuple[1], theme_tuple[2], theme_tuple[3]
    n_css, n_js, n_img, n_api = 2, 2, 2, 2
    css, js, imgs, api = _sub_resources(n_css, n_js, n_img, n_api, rng)
    total_resources = n_css + n_js + n_img + n_api

    posts_html = ""
    users = ["@traveller88", "@healthjunkie", "@urbanexplorer", "@techwriter", "@photogeek",
             "@homelab_nerd", "@bookworm", "@financefan", "@chef_life", "@retrotech"]
    for i in range(8):
        user = users[i % len(users)]
        kw   = kws[i % len(kws)]
        img_src = ALL_IMG[i % len(ALL_IMG)]
        likes = rng.randint(12, 4800)
        comments = rng.randint(2, 340)
        posts_html += f"""
      <div class="post-card">
        <div class="post-header">
          <img src="{img_src}" alt="avatar" width="40" height="40" class="avatar">
          <span class="username">{user}</span>
          <span class="post-time">{rng.randint(1,23)}h ago</span>
        </div>
        <p class="post-text">Exploring {kw} today — absolutely worth the effort. The details here are incredible. #{kw.replace(' ','_')} #{tag}</p>
        <div class="post-actions">
          <button>♥ {likes}</button>
          <button>💬 {comments}</button>
          <button>↗ Share</button>
        </div>
      </div>"""

    fetch_js = _fetch_script(api)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
  <meta http-equiv="Pragma" content="no-cache">
  <meta http-equiv="Expires" content="0">
  <title>{title} — Social Feed</title>
  <!-- {total_resources} sub-resources -->
{_css_links(css)}
{_js_tags(js)}
</head>
<body>
  <header class="social-header">
    <h1>#{tag}</h1>
    <p>{subtitle}</p>
  </header>
  <main class="feed-layout">
    <div class="post-feed">{posts_html}</div>
    <aside class="who-to-follow">
      <h3>Trending Tags</h3>
      <ul id="trending-list"><li>Loading…</li></ul>
    </aside>
  </main>
  <footer class="site-footer">
    <p>page_social_{page_num} &mdash; {total_resources} sub-resources</p>
  </footer>
  {fetch_js}
</body>
</html>"""


def render_forum_page(theme_tuple, page_num, rng):
    (thread_title, excerpt, board, subforum, kws) = theme_tuple
    n_css, n_js, n_img, n_api = 2, 2, 1, 2
    css, js, imgs, api = _sub_resources(n_css, n_js, n_img, n_api, rng)
    total_resources = n_css + n_js + n_img + n_api

    replies_html = ""
    handles = ["kernel_hacker", "devops_dave", "arch_btw", "nullptr", "root_user",
               "signal_noise", "async_await", "heap_spray", "bit_bandit", "cron_job"]
    for i in range(9):
        handle = handles[i % len(handles)]
        kw     = kws[i % len(kws)]
        upvotes = rng.randint(0, 214)
        field_for_para = subforum.lower()
        reply_text = _para(field_for_para, kws, rng, style="body")
        replies_html += f"""
      <div class="forum-post {'original-post' if i==0 else 'reply'}">
        <div class="post-meta">
          <strong class="handle">{handle}</strong>
          <span class="post-time">{rng.randint(1,48)}h ago</span>
          <span class="upvotes">▲ {upvotes}</span>
        </div>
        <div class="post-content">
          <p>{reply_text}</p>
          <code class="inline-code"># related to {kw}</code>
        </div>
      </div>"""

    fetch_js = _fetch_script(api)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
  <meta http-equiv="Pragma" content="no-cache">
  <meta http-equiv="Expires" content="0">
  <title>{thread_title} — {board} Forum</title>
  <!-- {total_resources} sub-resources -->
{_css_links(css)}
{_js_tags(js)}
</head>
<body>
  <header class="forum-header">
    <nav>Forums › {board} › {subforum}</nav>
  </header>
  <main class="forum-layout">
    <div class="thread-header">
      <h1>{thread_title}</h1>
      <p class="thread-excerpt">{excerpt}</p>
      <span class="board-tag">{board}</span>
      <span class="subforum-tag">{subforum}</span>
    </div>
    <div class="thread-body">{replies_html}</div>
    <div class="reply-form">
      <h3>Post a Reply</h3>
      <form action="#" method="post">
        <textarea name="body" rows="6" placeholder="Share your knowledge…"></textarea>
        <button type="submit">Submit Reply</button>
      </form>
    </div>
  </main>
  <footer class="site-footer">
    <p>page_forum_{page_num} &mdash; {total_resources} sub-resources</p>
  </footer>
  {fetch_js}
</body>
</html>"""


# ── JSON generators ────────────────────────────────────────────────────────
_DATA_JSON_TYPES = [
    "user_database", "product_catalog", "transaction_log", "event_log",
    "sensor_readings", "search_results", "analytics_report", "order_history",
    "inventory_snapshot", "audit_trail",
]

_USER_ROLES   = ["viewer","editor","admin","analyst","manager","developer","support","owner"]
_USER_REGIONS = ["EU-West","EU-East","NA-East","NA-West","APAC","LATAM","MENA","AFRICA"]
_USER_TIERS   = ["free","starter","professional","enterprise","trial"]
_CURRENCIES   = ["USD","EUR","GBP","JPY","CHF","CAD","AUD","SGD"]
_STATUSES     = ["pending","processing","completed","failed","refunded","disputed"]
_EVENT_TYPES  = ["login","logout","purchase","view","click","share","download","upload","search","error"]


def _iso(rng, base_days=365):
    dt = datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(
        days=rng.randint(0, base_days),
        seconds=rng.randint(0, 86399),
    )
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _hex_id(rng, length=16):
    return "".join(rng.choice("0123456789abcdef") for _ in range(length))


def gen_data_json(index, rng):
    dtype = _DATA_JSON_TYPES[index % len(_DATA_JSON_TYPES)]
    # target 50-200 KB, scale with index
    target_kb = 50 + ((index - 11) % 8) * 20   # 50,70,90,...,190 KB cycling
    target_bytes = target_kb * 1024

    if dtype == "user_database":
        records = []
        while True:
            uid = rng.randint(10000, 999999)
            rec = {
                "id": uid, "username": f"user_{uid}",
                "email": f"user{uid}@example.com",
                "first_name": rng.choice(["Alice","Bob","Carol","David","Eve","Frank","Grace","Hank","Iris","Jake"]),
                "last_name":  rng.choice(["Smith","Jones","Brown","Taylor","Wilson","Davies","Evans","Thomas","Roberts","Jackson"]),
                "created_at": _iso(rng, 1000), "last_login": _iso(rng, 30),
                "role": rng.choice(_USER_ROLES), "region": rng.choice(_USER_REGIONS),
                "tier": rng.choice(_USER_TIERS), "active": rng.choice([True,False]),
                "score": round(rng.uniform(0, 1000), 2),
                "followers": rng.randint(0, 50000), "following": rng.randint(0, 5000),
                "posts": rng.randint(0, 2000), "verified": rng.choice([True,False]),
                "country_code": rng.choice(["US","GB","DE","FR","JP","CA","AU","SG","IN","BR"]),
            }
            records.append(rec)
            if len(json.dumps(records)) >= target_bytes:
                break
        payload = {"type": "user_database", "count": len(records),
                   "generated_at": _iso(rng, 0), "users": records}

    elif dtype == "product_catalog":
        cats = ["Electronics","Clothing","Books","Home","Sports","Garden","Beauty","Automotive","Toys","Food"]
        records = []
        while True:
            pid = rng.randint(1000, 99999)
            rec = {
                "product_id": f"PROD-{pid}", "sku": f"SKU-{_hex_id(rng,8).upper()}",
                "name": f"Product {pid} — {rng.choice(cats)} Edition",
                "category": rng.choice(cats), "subcategory": f"Sub-{rng.randint(1,20)}",
                "price_usd": round(rng.uniform(4.99, 999.99), 2),
                "sale_price": round(rng.uniform(2.99, 799.99), 2),
                "currency": rng.choice(_CURRENCIES),
                "stock_qty": rng.randint(0, 5000),
                "rating": round(rng.uniform(1.0, 5.0), 1),
                "review_count": rng.randint(0, 10000),
                "weight_g": rng.randint(10, 20000),
                "created_at": _iso(rng, 1000), "updated_at": _iso(rng, 30),
                "active": rng.choice([True, True, True, False]),
                "tags": rng.sample(["sale","new","featured","clearance","bestseller","exclusive"], 2),
            }
            records.append(rec)
            if len(json.dumps(records)) >= target_bytes:
                break
        payload = {"type": "product_catalog", "count": len(records),
                   "generated_at": _iso(rng, 0), "products": records}

    elif dtype == "transaction_log":
        records = []
        while True:
            rec = {
                "tx_id": f"TX-{_hex_id(rng,12).upper()}",
                "timestamp": _iso(rng, 365),
                "sender_id": f"USR-{rng.randint(10000,999999)}",
                "receiver_id": f"USR-{rng.randint(10000,999999)}",
                "amount": round(rng.uniform(0.01, 50000), 2),
                "currency": rng.choice(_CURRENCIES),
                "status": rng.choice(_STATUSES),
                "fee": round(rng.uniform(0, 50), 2),
                "method": rng.choice(["card","bank_transfer","crypto","wallet","direct_debit"]),
                "ip_country": rng.choice(["US","GB","DE","FR","JP","NL","CA","AU"]),
                "risk_score": round(rng.uniform(0, 1), 3),
                "flagged": rng.random() < 0.03,
            }
            records.append(rec)
            if len(json.dumps(records)) >= target_bytes:
                break
        payload = {"type": "transaction_log", "count": len(records),
                   "generated_at": _iso(rng, 0), "transactions": records}

    elif dtype == "event_log":
        records = []
        while True:
            rec = {
                "event_id": _hex_id(rng, 20),
                "event_type": rng.choice(_EVENT_TYPES),
                "timestamp": _iso(rng, 90),
                "session_id": _hex_id(rng, 24),
                "user_id": f"USR-{rng.randint(1000,99999)}",
                "ip": f"{rng.randint(1,254)}.{rng.randint(0,254)}.{rng.randint(0,254)}.{rng.randint(1,254)}",
                "user_agent": rng.choice(["Mozilla/5.0 Chrome/120","Mozilla/5.0 Safari/16","curl/8.2"]),
                "path": f"/page/{rng.randint(1,500)}",
                "referrer": rng.choice(["direct","google.com","twitter.com","email","none"]),
                "duration_ms": rng.randint(1, 30000),
                "response_code": rng.choice([200,200,200,200,301,302,404,500]),
            }
            records.append(rec)
            if len(json.dumps(records)) >= target_bytes:
                break
        payload = {"type": "event_log", "count": len(records),
                   "generated_at": _iso(rng, 0), "events": records}

    elif dtype == "sensor_readings":
        sensor_ids = [f"SENSOR-{i:04d}" for i in range(1, 21)]
        records = []
        while True:
            sid = rng.choice(sensor_ids)
            rec = {
                "sensor_id": sid,
                "timestamp": _iso(rng, 30),
                "temperature_c": round(rng.uniform(-10, 80), 2),
                "humidity_pct": round(rng.uniform(5, 99), 1),
                "pressure_hpa": round(rng.uniform(900, 1100), 1),
                "pm25": round(rng.uniform(0, 200), 1),
                "co2_ppm": rng.randint(350, 5000),
                "battery_v": round(rng.uniform(2.8, 4.2), 2),
                "rssi_dbm": rng.randint(-100, -30),
                "location": {"lat": round(rng.uniform(-90, 90), 4),
                             "lon": round(rng.uniform(-180, 180), 4)},
                "anomaly": rng.random() < 0.05,
            }
            records.append(rec)
            if len(json.dumps(records)) >= target_bytes:
                break
        payload = {"type": "sensor_readings", "count": len(records),
                   "generated_at": _iso(rng, 0), "readings": records}

    elif dtype == "search_results":
        queries = ["machine learning","climate change","renewable energy","blockchain",
                   "quantum computing","cybersecurity","autonomous vehicles","gene therapy"]
        query = rng.choice(queries)
        results = []
        while True:
            rid = rng.randint(100000, 9999999)
            rec = {
                "rank": len(results) + 1,
                "result_id": f"RES-{rid}",
                "title": f"Result about {query} — document {rid}",
                "url": f"https://example.com/docs/{rid}",
                "snippet": f"This document covers key aspects of {query} including methodology, findings, and implications for practice. See section {rng.randint(1,10)} for details.",
                "domain": rng.choice(["example.com","research.org","papers.io","docs.net","archive.edu"]),
                "score": round(rng.uniform(0.5, 1.0), 4),
                "published": _iso(rng, 1000),
                "word_count": rng.randint(500, 15000),
                "language": "en",
                "cached": rng.choice([True, False]),
            }
            results.append(rec)
            if len(json.dumps(results)) >= target_bytes:
                break
        payload = {"type": "search_results", "query": query, "total_hits": len(results) * rng.randint(2, 20),
                   "page": 1, "per_page": len(results),
                   "generated_at": _iso(rng, 0), "results": results}

    elif dtype == "analytics_report":
        days = 90
        ts_start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        daily = []
        for d in range(days):
            day = (ts_start + timedelta(days=d)).strftime("%Y-%m-%d")
            daily.append({
                "date": day,
                "sessions": rng.randint(800, 50000),
                "pageviews": rng.randint(2000, 200000),
                "unique_users": rng.randint(500, 40000),
                "bounce_rate": round(rng.uniform(0.2, 0.8), 3),
                "avg_session_s": rng.randint(30, 600),
                "conversions": rng.randint(0, 2000),
                "revenue_usd": round(rng.uniform(0, 50000), 2),
                "new_users": rng.randint(100, 10000),
                "returning_users": rng.randint(200, 30000),
                "mobile_pct": round(rng.uniform(0.3, 0.75), 3),
            })
        pages = []
        for _ in range(50):
            pid2 = rng.randint(1, 500)
            pages.append({
                "path": f"/page/{pid2}",
                "views": rng.randint(100, 100000),
                "avg_time_s": rng.randint(10, 400),
                "exits": rng.randint(10, 5000),
            })
        while len(json.dumps({"daily": daily, "pages": pages})) < target_bytes:
            pages.append({"path": f"/extra/{rng.randint(500,9999)}",
                          "views": rng.randint(1, 100),
                          "avg_time_s": rng.randint(5, 200),
                          "exits": rng.randint(1, 50)})
        payload = {"type": "analytics_report", "period": "90d",
                   "generated_at": _iso(rng, 0), "daily": daily, "top_pages": pages}

    elif dtype == "order_history":
        records = []
        while True:
            oid = rng.randint(100000, 9999999)
            n_items = rng.randint(1, 6)
            items = [{"sku": f"SKU-{_hex_id(rng,6).upper()}",
                       "qty": rng.randint(1,5),
                       "unit_price": round(rng.uniform(1,500),2)} for _ in range(n_items)]
            rec = {
                "order_id": f"ORD-{oid}",
                "user_id": f"USR-{rng.randint(10000,999999)}",
                "placed_at": _iso(rng, 365),
                "status": rng.choice(_STATUSES),
                "items": items,
                "subtotal": round(sum(i["qty"]*i["unit_price"] for i in items), 2),
                "shipping_usd": round(rng.uniform(0, 25), 2),
                "tax_usd": round(rng.uniform(0, 80), 2),
                "currency": rng.choice(_CURRENCIES),
                "shipping_country": rng.choice(["US","GB","DE","FR","NL","CA","AU"]),
                "carrier": rng.choice(["UPS","FedEx","DHL","Royal Mail","AusPost"]),
                "tracking_id": _hex_id(rng, 12).upper(),
            }
            records.append(rec)
            if len(json.dumps(records)) >= target_bytes:
                break
        payload = {"type": "order_history", "count": len(records),
                   "generated_at": _iso(rng, 0), "orders": records}

    elif dtype == "inventory_snapshot":
        warehouses = ["WH-LON","WH-NYC","WH-SIN","WH-FRA","WH-SYD","WH-TOK"]
        records = []
        while True:
            pid = rng.randint(1000, 99999)
            rec = {
                "sku": f"SKU-{pid}",
                "warehouse": rng.choice(warehouses),
                "qty_on_hand": rng.randint(0, 10000),
                "qty_reserved": rng.randint(0, 500),
                "qty_incoming": rng.randint(0, 2000),
                "reorder_point": rng.randint(10, 500),
                "lead_time_days": rng.randint(1, 45),
                "unit_cost_usd": round(rng.uniform(0.5, 500), 2),
                "last_counted": _iso(rng, 30),
                "location_code": f"AISLE-{rng.randint(1,50)}-{rng.choice('ABCDEF')}{rng.randint(1,20)}",
                "expiry": _iso(rng, 730) if rng.random() < 0.3 else None,
            }
            records.append(rec)
            if len(json.dumps(records)) >= target_bytes:
                break
        payload = {"type": "inventory_snapshot", "count": len(records),
                   "snapshot_at": _iso(rng, 0), "items": records}

    else:  # audit_trail
        actions = ["create","read","update","delete","login","logout","export","import","approve","reject"]
        records = []
        while True:
            rec = {
                "audit_id": _hex_id(rng, 18),
                "timestamp": _iso(rng, 180),
                "actor_id": f"USR-{rng.randint(100,9999)}",
                "action": rng.choice(actions),
                "resource_type": rng.choice(["User","Order","Product","Invoice","Report","Config"]),
                "resource_id": str(rng.randint(1000, 999999)),
                "ip": f"{rng.randint(1,254)}.{rng.randint(0,254)}.{rng.randint(0,254)}.{rng.randint(1,254)}",
                "result": rng.choice(["success","success","success","denied","error"]),
                "changes": {f"field_{j}": {"old": rng.randint(0,100), "new": rng.randint(0,100)}
                            for j in range(rng.randint(1,5))},
            }
            records.append(rec)
            if len(json.dumps(records)) >= target_bytes:
                break
        payload = {"type": "audit_trail", "count": len(records),
                   "generated_at": _iso(rng, 0), "events": records}

    return json.dumps(payload, separators=(",", ":"))


# ── Crypto / API JSON generators ──────────────────────────────────────────
_TOKENS = ["BTC","ETH","SOL","AVAX","DOT","LINK","UNI","MATIC","ARB","OP",
           "INJ","SEI","TIA","DYDX","PYTH","JUP","STRK","WIF","BONK","PEPE"]
_PAIRS  = [f"{t}/USDT" for t in _TOKENS]

def _ohlcv(rng, n, base_price):
    rows = []
    price = base_price
    ts    = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp()) * 1000
    for _ in range(n):
        price *= rng.uniform(0.97, 1.03)
        high   = price * rng.uniform(1.001, 1.02)
        low    = price * rng.uniform(0.98, 0.999)
        close  = rng.uniform(low, high)
        vol    = rng.uniform(1e5, 1e9)
        rows.append([ts, round(price,4), round(high,4), round(low,4), round(close,4), round(vol,2)])
        ts += 3600_000
    return rows


def gen_crypto_market_data(index, rng):
    pair   = _PAIRS[index % len(_PAIRS)]
    token  = pair.split("/")[0]
    base   = rng.uniform(0.01, 65000)
    n_candles = 720   # 30 days of hourly data
    candles = _ohlcv(rng, n_candles, base)
    stats = {
        "market_cap_usd":    round(base * rng.uniform(1e7, 5e11), 0),
        "volume_24h_usd":    round(rng.uniform(1e6, 1e10), 0),
        "circulating_supply": rng.randint(int(1e6), int(2e10)),
        "max_supply":         rng.randint(int(1e6), int(2e10)),
        "rank":               rng.randint(1, 500),
        "ath_usd":            round(base * rng.uniform(1.0, 10.0), 4),
        "atl_usd":            round(base * rng.uniform(0.01, 0.99), 6),
        "price_change_24h":   round(rng.uniform(-0.15, 0.15), 4),
        "price_change_7d":    round(rng.uniform(-0.30, 0.30), 4),
        "dominance":          round(rng.uniform(0.001, 0.50), 4),
    }
    payload = {
        "pair": pair, "token": token, "quote": "USDT",
        "interval": "1h", "limit": n_candles,
        "generated_at": _iso(rng, 0),
        "stats": stats,
        "ohlcv": candles,
    }
    return json.dumps(payload, separators=(",",":"))


def gen_crypto_portfolio(index, rng):
    n_assets = rng.randint(8, 20)
    assets = []
    for i in range(n_assets):
        token = _TOKENS[i % len(_TOKENS)]
        qty   = round(rng.uniform(0.001, 10000), 6)
        price = round(rng.uniform(0.01, 65000), 4)
        assets.append({
            "token": token, "qty": qty,
            "avg_cost_usd": round(price * rng.uniform(0.5, 2.0), 4),
            "current_price_usd": price,
            "value_usd": round(qty * price, 2),
            "pnl_pct": round(rng.uniform(-0.8, 5.0), 4),
            "pnl_usd": round(qty * price * rng.uniform(-0.8, 5.0), 2),
            "allocation_pct": 0.0,
        })
    total = sum(a["value_usd"] for a in assets)
    for a in assets:
        a["allocation_pct"] = round(a["value_usd"] / total * 100, 2) if total else 0

    history_days = 180
    hist = []
    val  = total * rng.uniform(0.3, 1.5)
    ts   = datetime(2025, 1, 1, tzinfo=timezone.utc)
    for d in range(history_days):
        val *= rng.uniform(0.97, 1.04)
        hist.append({"date": (ts + timedelta(days=d)).strftime("%Y-%m-%d"),
                     "total_usd": round(val, 2),
                     "deposited_usd": round(rng.uniform(0, 5000), 2),
                     "withdrawn_usd": round(rng.uniform(0, 1000), 2)})

    performance = {
        "total_value_usd":    round(total, 2),
        "total_cost_usd":     round(total * rng.uniform(0.6, 1.5), 2),
        "unrealised_pnl_usd": round(total * rng.uniform(-0.3, 2.0), 2),
        "realised_pnl_usd":   round(rng.uniform(-5000, 50000), 2),
        "roi_pct":            round(rng.uniform(-50, 400), 2),
        "sharpe_ratio":       round(rng.uniform(-1, 4), 3),
        "max_drawdown_pct":   round(rng.uniform(0.05, 0.90), 3),
        "win_rate":           round(rng.uniform(0.3, 0.75), 3),
    }
    payload = {
        "portfolio_id": _hex_id(rng, 12),
        "user_id": f"USR-{rng.randint(1000,99999)}",
        "generated_at": _iso(rng, 0),
        "assets": assets,
        "performance": performance,
        "history": hist,
    }
    return json.dumps(payload, separators=(",",":"))


def gen_crypto_orderbook(index, rng):
    pair   = _PAIRS[index % len(_PAIRS)]
    mid    = rng.uniform(0.01, 65000)
    depth  = rng.randint(200, 500)

    bids, price = [], mid
    for _ in range(depth):
        price *= rng.uniform(0.997, 0.9999)
        qty    = round(rng.uniform(0.001, 500), 6)
        bids.append([round(price, 6), qty])

    asks, price = [], mid
    for _ in range(depth):
        price *= rng.uniform(1.0001, 1.003)
        qty    = round(rng.uniform(0.001, 500), 6)
        asks.append([round(price, 6), qty])

    recent_trades = []
    for _ in range(200):
        recent_trades.append({
            "id": _hex_id(rng, 12),
            "timestamp": _iso(rng, 1),
            "price": round(mid * rng.uniform(0.995, 1.005), 6),
            "qty": round(rng.uniform(0.001, 100), 6),
            "side": rng.choice(["buy","sell"]),
            "maker": rng.choice([True,False]),
        })

    payload = {
        "pair": pair, "exchange": rng.choice(["Binance","Coinbase","Kraken","OKX","Bybit"]),
        "timestamp": _iso(rng, 0),
        "mid_price": round(mid, 6),
        "spread": round(mid * 0.0002, 6),
        "bid_depth": depth, "ask_depth": depth,
        "bids": bids, "asks": asks,
        "recent_trades": recent_trades,
    }
    return json.dumps(payload, separators=(",",":"))


def gen_crypto_analytics(index, rng):
    n_tokens = 10
    tokens   = _TOKENS[:n_tokens]
    days     = 60

    # daily volume per token
    volume_history = {}
    for t in tokens:
        daily = []
        vol   = rng.uniform(1e6, 1e9)
        ts    = datetime(2025, 1, 1, tzinfo=timezone.utc)
        for d in range(days):
            vol *= rng.uniform(0.85, 1.18)
            daily.append({"date": (ts + timedelta(days=d)).strftime("%Y-%m-%d"),
                          "volume_usd": round(vol, 0),
                          "trades": rng.randint(1000, 500000),
                          "unique_wallets": rng.randint(100, 50000)})
        volume_history[t] = daily

    # correlation matrix
    corr = {}
    for a in tokens:
        corr[a] = {}
        for b in tokens:
            corr[a][b] = 1.0 if a == b else round(rng.uniform(-0.3, 0.98), 4)

    # whale transactions
    whales = []
    for _ in range(150):
        t = rng.choice(tokens)
        whales.append({
            "tx_hash": _hex_id(rng, 20),
            "token": t,
            "amount_usd": round(rng.uniform(1e5, 1e8), 0),
            "timestamp": _iso(rng, 30),
            "from_type": rng.choice(["exchange","whale_wallet","unknown","defi_protocol"]),
            "to_type":   rng.choice(["exchange","whale_wallet","unknown","cold_storage"]),
        })

    payload = {
        "type": "analytics", "generated_at": _iso(rng, 0),
        "period_days": days, "tokens": tokens,
        "volume_history": volume_history,
        "correlation_matrix": corr,
        "whale_transactions": whales,
    }
    return json.dumps(payload, separators=(",",":"))


def gen_crypto_metrics(index, rng):
    n_days = 90
    ts     = datetime(2025, 1, 1, tzinfo=timezone.utc)

    hashrate, difficulty, mempool, fees = [], [], [], []
    hr  = rng.uniform(3e17, 6e17)
    dif = rng.uniform(5e13, 9e13)
    mp  = rng.randint(5000, 80000)
    for d in range(n_days):
        day_str = (ts + timedelta(days=d)).strftime("%Y-%m-%d")
        hr  *= rng.uniform(0.97, 1.04)
        dif *= rng.uniform(0.98, 1.03)
        mp  = max(1000, int(mp * rng.uniform(0.9, 1.15)))
        hashrate.append(  {"date": day_str, "value": round(hr, 0)})
        difficulty.append({"date": day_str, "value": round(dif, 0)})
        mempool.append(   {"date": day_str, "count": mp,
                           "size_mb": round(mp * rng.uniform(0.5, 1.5) / 1000, 2)})
        fees.append(      {"date": day_str,
                           "median_sat_per_vbyte": round(rng.uniform(1, 200), 1),
                           "mean_sat_per_vbyte":   round(rng.uniform(2, 300), 1),
                           "p90_sat_per_vbyte":    round(rng.uniform(5, 500), 1)})

    node_distribution = {
        "total_reachable": rng.randint(10000, 20000),
        "by_country": {c: rng.randint(50, 3000) for c in ["US","DE","FR","NL","GB","JP","CA","SG","AU","CN"]},
        "by_version": {f"v{rng.randint(23,27)}.{rng.randint(0,9)}.{rng.randint(0,5)}": rng.randint(100, 5000)
                       for _ in range(6)},
    }
    lightning = {
        "channels": rng.randint(50000, 100000),
        "capacity_btc": round(rng.uniform(3000, 6000), 2),
        "nodes": rng.randint(10000, 20000),
        "avg_channel_size_sat": rng.randint(500000, 5000000),
    }

    payload = {
        "type": "network_metrics", "chain": rng.choice(["Bitcoin","Ethereum","Solana"]),
        "generated_at": _iso(rng, 0), "period_days": n_days,
        "hashrate": hashrate, "difficulty": difficulty,
        "mempool": mempool, "fees": fees,
        "node_distribution": node_distribution,
        "lightning_network": lightning,
    }
    return json.dumps(payload, separators=(",",":"))


# ── Main ───────────────────────────────────────────────────────────────────
def main():
    generated = []

    # 1. page_html_heavy_11 to _50
    print("Generating page_html_heavy_11..50 (40 pages)…")
    for idx, theme in enumerate(HEAVY_THEMES):
        page_num = 11 + idx
        rng = random.Random(2026 + page_num)
        html = render_heavy_page(theme, page_num, rng)
        fname = f"page_html_heavy_{page_num}.html"
        path  = os.path.join(OUT_DIR, fname)
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        generated.append(fname)
        size_kb = os.path.getsize(path) / 1024
        print(f"  {fname}  {size_kb:.1f} KB")

    # 2. page_news_6..20
    print("\nGenerating page_news_6..20 (15 pages)…")
    for i, theme in enumerate(NEWS_THEMES):
        page_num = 6 + i
        rng = random.Random(3000 + page_num)
        html = render_news_page(theme, page_num, rng)
        fname = f"page_news_{page_num}.html"
        path  = os.path.join(OUT_DIR, fname)
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        generated.append(fname)
        print(f"  {fname}  {os.path.getsize(path)/1024:.1f} KB")

    # 3. page_shop_6..20
    print("\nGenerating page_shop_6..20 (15 pages)…")
    for i, theme in enumerate(SHOP_THEMES):
        page_num = 6 + i
        rng = random.Random(4000 + page_num)
        html = render_shop_page(theme, page_num, rng)
        fname = f"page_shop_{page_num}.html"
        path  = os.path.join(OUT_DIR, fname)
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        generated.append(fname)
        print(f"  {fname}  {os.path.getsize(path)/1024:.1f} KB")

    # 4. page_social_6..20
    print("\nGenerating page_social_6..20 (15 pages)…")
    for i, theme in enumerate(SOCIAL_THEMES):
        page_num = 6 + i
        rng = random.Random(5000 + page_num)
        html = render_social_page(theme, page_num, rng)
        fname = f"page_social_{page_num}.html"
        path  = os.path.join(OUT_DIR, fname)
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        generated.append(fname)
        print(f"  {fname}  {os.path.getsize(path)/1024:.1f} KB")

    # 5. page_forum_6..20
    print("\nGenerating page_forum_6..20 (15 pages)…")
    for i, theme in enumerate(FORUM_THEMES):
        page_num = 6 + i
        rng = random.Random(6000 + page_num)
        html = render_forum_page(theme, page_num, rng)
        fname = f"page_forum_{page_num}.html"
        path  = os.path.join(OUT_DIR, fname)
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        generated.append(fname)
        print(f"  {fname}  {os.path.getsize(path)/1024:.1f} KB")

    # 6. data_json_11..50
    print("\nGenerating data_json_11..50 (40 files)…")
    for idx in range(11, 51):
        rng   = random.Random(7000 + idx)
        data  = gen_data_json(idx, rng)
        fname = f"data_json_{idx}.json"
        path  = os.path.join(OUT_DIR, fname)
        with open(path, "w", encoding="utf-8") as f:
            f.write(data)
        generated.append(fname)
        print(f"  {fname}  {os.path.getsize(path)/1024:.1f} KB")

    # 7. crypto_market_data_1..8
    print("\nGenerating crypto_market_data_1..8 (8 files)…")
    for idx in range(1, 9):
        rng   = random.Random(8000 + idx)
        data  = gen_crypto_market_data(idx, rng)
        fname = f"crypto_market_data_{idx}.json"
        path  = os.path.join(OUT_DIR, fname)
        with open(path, "w", encoding="utf-8") as f:
            f.write(data)
        generated.append(fname)
        print(f"  {fname}  {os.path.getsize(path)/1024:.1f} KB")

    # 8. crypto_portfolio_1..8
    print("\nGenerating crypto_portfolio_1..8 (8 files)…")
    for idx in range(1, 9):
        rng   = random.Random(8100 + idx)
        data  = gen_crypto_portfolio(idx, rng)
        fname = f"crypto_portfolio_{idx}.json"
        path  = os.path.join(OUT_DIR, fname)
        with open(path, "w", encoding="utf-8") as f:
            f.write(data)
        generated.append(fname)
        print(f"  {fname}  {os.path.getsize(path)/1024:.1f} KB")

    # 9. crypto_orderbook_1..8
    print("\nGenerating crypto_orderbook_1..8 (8 files)…")
    for idx in range(1, 9):
        rng   = random.Random(8200 + idx)
        data  = gen_crypto_orderbook(idx, rng)
        fname = f"crypto_orderbook_{idx}.json"
        path  = os.path.join(OUT_DIR, fname)
        with open(path, "w", encoding="utf-8") as f:
            f.write(data)
        generated.append(fname)
        print(f"  {fname}  {os.path.getsize(path)/1024:.1f} KB")

    # 10. crypto_analytics_1..8
    print("\nGenerating crypto_analytics_1..8 (8 files)…")
    for idx in range(1, 9):
        rng   = random.Random(8300 + idx)
        data  = gen_crypto_analytics(idx, rng)
        fname = f"crypto_analytics_{idx}.json"
        path  = os.path.join(OUT_DIR, fname)
        with open(path, "w", encoding="utf-8") as f:
            f.write(data)
        generated.append(fname)
        print(f"  {fname}  {os.path.getsize(path)/1024:.1f} KB")

    # 11. crypto_metrics_1..8
    print("\nGenerating crypto_metrics_1..8 (8 files)…")
    for idx in range(1, 9):
        rng   = random.Random(8400 + idx)
        data  = gen_crypto_metrics(idx, rng)
        fname = f"crypto_metrics_{idx}.json"
        path  = os.path.join(OUT_DIR, fname)
        with open(path, "w", encoding="utf-8") as f:
            f.write(data)
        generated.append(fname)
        print(f"  {fname}  {os.path.getsize(path)/1024:.1f} KB")

    print(f"\nTotal files generated: {len(generated)}")
    return generated


if __name__ == "__main__":
    main()
