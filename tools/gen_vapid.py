"""Web Push용 VAPID 키 한 쌍을 생성합니다. (최초 1회)

    pip install cryptography
    python tools/gen_vapid.py

출력된 두 값을 GitHub 저장소 Settings → Secrets and variables → Actions 에 등록하세요.
  - VAPID_PRIVATE_KEY : 비공개 (절대 공유 금지)
  - VAPID_PUBLIC_KEY  : 공개키 (앱이 푸시 구독할 때 사용 — Variables 탭에도 같은 값 등록)
"""
import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


key = ec.generate_private_key(ec.SECP256R1())
priv = key.private_numbers().private_value.to_bytes(32, "big")
pub = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)

print("VAPID_PRIVATE_KEY =", b64u(priv))
print("VAPID_PUBLIC_KEY  =", b64u(pub))
