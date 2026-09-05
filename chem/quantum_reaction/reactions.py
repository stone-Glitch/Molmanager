"""预设反应库。

每个反应含 name/description/reactants/products。分子用 SMILES 表示（rdkit 会生成 3D +
MMFF 优化的初始 XYZ），重原子+氢自动补齐。原子数配平用整数倍保证两侧原子总数相同。

对单原子/双原子小分子，SMILES 写法：
  H2 -> "[H][H]"
  O2 -> "O=O"
  N2 -> "N#N"
  Cl2 -> "ClCl"
  HCl -> "Cl"  (rdkit 自动加 H)
  H2O -> "O"
  NH3 -> "N"
  CH4 -> "C"
  CO2 -> "O=C=O"
  CO -> "[C-]#[O+]"
  C2H4 (乙烯) -> "C=C"
  C2H6 (乙烷) -> "CC"
  CH3OH -> "CO"
  CH3OCH3 -> "COC"
  O3 -> "[O-][O+]=O"
"""

# 标注：每项 reactants/products 是 list[dict]。
#   dict 字段：smiles（必填，可重复表示化学计量数）, label（展示用）,
#             multiplicity（可选，自旋多重度，默认 1）
# 多次出现的同一分子按 list 中重复条目处理（化学计量数 = 出现次数）
#
# ⚠ 多重度说明（化学正确性）：
#   O₂ 基态是三线态（两个不成对电子），必须标 multiplicity=3。
#   用 singlet 优化 O₂ 会得到激发态（能量偏高 ~40 kJ/mol），
#   导致涉及 O₂ 的反应 ΔE 系统性偏差。
#   O₃ 基态是 singlet，H₂/N₂/闭壳层小分子均 singlet，无需标注。

REACTIONS = [
    {
        "id": "water",
        "name": "水生成",
        "equation": "2 H₂ + O₂ → 2 H₂O",
        "description": "氢气与氧气反应生成水。最经典的氧化反应，ΔE 显著负值。",
        "reactants": [
            {"smiles": "[H][H]", "label": "H₂"},
            {"smiles": "[H][H]", "label": "H₂"},
            {"smiles": "O=O", "label": "O₂", "multiplicity": 3},
        ],
        "products": [
            {"smiles": "O", "label": "H₂O"},
            {"smiles": "O", "label": "H₂O"},
        ],
    },
    {
        "id": "ammonia",
        "name": "氨合成（Haber 法）",
        "equation": "N₂ + 3 H₂ → 2 NH₃",
        "description": "工业合成氨的经典反应，高压催化。",
        "reactants": [
            {"smiles": "N#N", "label": "N₂"},
            {"smiles": "[H][H]", "label": "H₂"},
            {"smiles": "[H][H]", "label": "H₂"},
            {"smiles": "[H][H]", "label": "H₂"},
        ],
        "products": [
            {"smiles": "N", "label": "NH₃"},
            {"smiles": "N", "label": "NH₃"},
        ],
    },
    {
        "id": "methane_burn",
        "name": "甲烷燃烧",
        "equation": "CH₄ + 2 O₂ → CO₂ + 2 H₂O",
        "description": "天然气燃烧，强放热。原子数较大，B3LYP 计算耗时长。",
        "reactants": [
            {"smiles": "C", "label": "CH₄"},
            {"smiles": "O=O", "label": "O₂", "multiplicity": 3},
            {"smiles": "O=O", "label": "O₂", "multiplicity": 3},
        ],
        "products": [
            {"smiles": "O=C=O", "label": "CO₂"},
            {"smiles": "O", "label": "H₂O"},
            {"smiles": "O", "label": "H₂O"},
        ],
    },
    {
        "id": "hcl_decomp",
        "name": "氯化氢分解",
        "equation": "2 HCl → H₂ + Cl₂",
        "description": "HCl 分解为单质，强吸热反应。",
        "reactants": [
            {"smiles": "Cl", "label": "HCl"},
            {"smiles": "Cl", "label": "HCl"},
        ],
        "products": [
            {"smiles": "[H][H]", "label": "H₂"},
            {"smiles": "ClCl", "label": "Cl₂"},
        ],
    },
    {
        "id": "ethylene_hydrogenation",
        "name": "乙烯加氢",
        "equation": "C₂H₄ + H₂ → C₂H₆",
        "description": "乙烯催化加氢生成乙烷，加成反应。",
        "reactants": [
            {"smiles": "C=C", "label": "C₂H₄"},
            {"smiles": "[H][H]", "label": "H₂"},
        ],
        "products": [
            {"smiles": "CC", "label": "C₂H₆"},
        ],
    },
    {
        "id": "water_gas_shift",
        "name": "水煤气变换",
        "equation": "CO + H₂O → CO₂ + H₂",
        "description": "工业制氢重要反应，温和放热。",
        "reactants": [
            {"smiles": "[C-]#[O+]", "label": "CO"},
            {"smiles": "O", "label": "H₂O"},
        ],
        "products": [
            {"smiles": "O=C=O", "label": "CO₂"},
            {"smiles": "[H][H]", "label": "H₂"},
        ],
    },
    {
        "id": "methanol_dehydrate",
        "name": "甲醇脱水",
        "equation": "2 CH₃OH → CH₃OCH₃ + H₂O",
        "description": "两分子甲醇脱水生成二甲醚和水。",
        "reactants": [
            {"smiles": "CO", "label": "CH₃OH"},
            {"smiles": "CO", "label": "CH₃OH"},
        ],
        "products": [
            {"smiles": "COC", "label": "CH₃OCH₃"},
            {"smiles": "O", "label": "H₂O"},
        ],
    },
    {
        "id": "ozone_decomp",
        "name": "臭氧分解",
        "equation": "2 O₃ → 3 O₂",
        "description": "臭氧分解为氧气，平流层反应的关键过程。",
        "reactants": [
            {"smiles": "[O-][O+]=O", "label": "O₃"},
            {"smiles": "[O-][O+]=O", "label": "O₃"},
        ],
        "products": [
            {"smiles": "O=O", "label": "O₂", "multiplicity": 3},
            {"smiles": "O=O", "label": "O₂", "multiplicity": 3},
            {"smiles": "O=O", "label": "O₂", "multiplicity": 3},
        ],
    },
]


def list_reactions():
    """返回前端可用的反应摘要列表。"""
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "equation": r["equation"],
            "description": r["description"],
            "reactant_count": len(r["reactants"]),
            "product_count": len(r["products"]),
        }
        for r in REACTIONS
    ]


def get_reaction(rid):
    """按 id 查找反应。"""
    for r in REACTIONS:
        if r["id"] == rid:
            return r
    return None
