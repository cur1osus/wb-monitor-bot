import hashlib
import hmac
from urllib.parse import parse_qsl

def validate_init_data(init_data: str, bot_token: str) -> dict | None:
    """
    Validate Telegram Web App init data.
    See: https://core.telegram.org/bots/webapps#validating-data-received-from-the-web-app
    """
    try:
        parsed_data = dict(parse_qsl(init_data))
        if "hash" not in parsed_data:
            return None
        
        received_hash = parsed_data.pop("hash")
        data_check_string = "\n".join(
            f"{k}={v}" for k, v in sorted(parsed_data.items())
        )
        
        secret_key = hmac.new(
            b"WebAppData", bot_token.encode(), hashlib.sha256
        ).digest()
        
        calculated_hash = hmac.new(
            secret_key, data_check_string.encode(), hashlib.sha256
        ).hexdigest()
        
        if calculated_hash != received_hash:
            return None
            
        return parsed_data
    except Exception:
        return None
