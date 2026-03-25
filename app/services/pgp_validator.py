"""Structural-only validation of PGP public key armour.

This module MUST NOT import gnupg, perform any crypto operations,
or attempt to parse/import key material. It only checks string structure.
"""

_PUBLIC_KEY_HEADER = "-----BEGIN PGP PUBLIC KEY BLOCK-----"
_PUBLIC_KEY_FOOTER = "-----END PGP PUBLIC KEY BLOCK-----"
_PRIVATE_KEY_MARKERS = (
    "-----BEGIN PGP PRIVATE KEY BLOCK-----",
    "-----BEGIN PGP SECRET KEY BLOCK-----",
)


def validate_public_key_armor(armored_key: str) -> bool:
    """Return True only if the string looks like a PGP public key block.

    Checks:
    - Contains no null bytes
    - Does not contain any private key markers
    - Starts with the public key header
    - Contains the public key footer
    - Has non-trivial content between header and footer
    """
    if not armored_key or "\x00" in armored_key:
        return False

    # Reject anything that looks like a private key — fail secure
    for marker in _PRIVATE_KEY_MARKERS:
        if marker in armored_key:
            return False

    stripped = armored_key.strip()
    if not stripped.startswith(_PUBLIC_KEY_HEADER):
        return False
    if _PUBLIC_KEY_FOOTER not in stripped:
        return False

    # Ensure there is actual content between header and footer
    header_end = stripped.index(_PUBLIC_KEY_HEADER) + len(_PUBLIC_KEY_HEADER)
    footer_start = stripped.index(_PUBLIC_KEY_FOOTER)
    body = stripped[header_end:footer_start].strip()
    if len(body) < 32:  # noqa: PLR2004 — minimum plausible base64 content
        return False

    return True
