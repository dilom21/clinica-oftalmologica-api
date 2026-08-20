from pwdlib import PasswordHash

password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verificar_password(password: str, password_guardada: str) -> bool:
    return password_hash.verify(password, password_guardada)