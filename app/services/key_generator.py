import secrets


def generate_api_key(slug: str) -> str:
    """Generate key in format: key-{slug}-{16 random hex chars}
    Example: key-erp-system-a3f8b2c1d4e5f6a7"""
    random_part = secrets.token_hex(8)  # 16 hex chars
    return f"key-{slug}-{random_part}"


def mask_api_key(key: str) -> str:
    """Mask all but last 6 characters of API key."""
    if len(key) <= 6:
        return key
    return f"{'*' * (len(key) - 6)}{key[-6:]}"
