from __future__ import annotations

import re


def extract_mechanisms(bullets: list) -> str:
    """Pull probable SAP mechanism names from solution bullets."""
    keywords = set()
    patterns = [
        r"\bBAPI_\w+",
        r"\bBAdI\s+\w+",
        r"\bBRF\+",
        r"\bCDS\b",
        r"\bOData\b",
        r"\bIDOC\s+\w+",
        r"\bIDoc\s+\w+",
        r"\bLSMW\b",
        r"\bFICA?\b",
        r"\bFPE1\b",
        r"\bFPG1\b",
        r"\bFQZ0\b",
        r"\bEASABI\b",
        r"\bEASIBI\b",
        r"\bEMIGALL\b",
        r"\bABAP\b",
        r"\bEC50E\b",
        r"\bFiori\b",
        r"\bPFCG\b",
        r"\bEMMA\b",
        r"\bEDM\b",
        r"\bRBAC\b",
        r"ISU_\w+",
        r"FKK_\w+",
        r"CL_\w+",
    ]
    for bullet in bullets:
        for pattern in patterns:
            keywords.update(re.findall(pattern, bullet, re.IGNORECASE))
    return ", ".join(sorted(keywords)) if keywords else ""
