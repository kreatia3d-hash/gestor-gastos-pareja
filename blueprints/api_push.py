import os

from flask import Blueprint, request, jsonify
from .helpers import _nido, get_db

bp = Blueprint('push', __name__)

# ── Firebase Admin SDK (lazy init) ───────────────────────────────────────────

_firebase_ready = False


def _init_firebase():
    global _firebase_ready
    if _firebase_ready:
        return True
    service_json = os.environ.get('FIREBASE_SERVICE_ACCOUNT', '').strip()
    if not service_json:
        return False
    try:
        import firebase_admin
        from firebase_admin import credentials
        import json as _json
        if not firebase_admin._apps:
            cred = credentials.Certificate(_json.loads(service_json))
            firebase_admin.initialize_app(cred)
        _firebase_ready = True
        return True
    except Exception:
        return False


# ── Helper público: enviar push a todo el nido excepto un usuario ─────────────

def enviar_push_nido(nido_id: int, excluir_uid: int, titulo: str, cuerpo: str):
    """Envía FCM push a todos los tokens del nido excepto el usuario que actúa.
    Diseñado para ejecutarse en un hilo secundario (no bloquea la respuesta HTTP).
    """
    try:
        if not _init_firebase():
            return

        from firebase_admin import messaging

        conn = get_db()
        rows = conn.execute(
            "SELECT id, token FROM fcm_tokens WHERE nido_id=? AND usuario_id!=?",
            (nido_id, excluir_uid)
        ).fetchall()
        conn.close()

        if not rows:
            return

        tokens = [r['token'] for r in rows]
        ids_por_token = {r['token']: r['id'] for r in rows}

        msg = messaging.MulticastMessage(
            notification=messaging.Notification(title=titulo, body=cuerpo),
            android=messaging.AndroidConfig(
                priority='high',
                notification=messaging.AndroidNotification(
                    channel_id='gastos_channel',
                    priority='high',
                ),
            ),
            apns=messaging.APNSConfig(
                payload=messaging.APNSPayload(
                    aps=messaging.Aps(sound='default', badge=1)
                )
            ),
            tokens=tokens,
        )

        response = messaging.send_each_for_multicast(msg)

        # Limpiar tokens inválidos (app desinstalada)
        invalidos = [
            tokens[i] for i, r in enumerate(response.responses)
            if not r.success and r.exception and
            'registration-token-not-registered' in str(r.exception)
        ]
        if invalidos:
            conn2 = get_db()
            for t in invalidos:
                conn2.execute("DELETE FROM fcm_tokens WHERE token=?", (t,))
            conn2.commit()
            conn2.close()

    except Exception:
        pass


# ── Endpoint: registrar / actualizar token FCM ────────────────────────────────

@bp.route('/api/push/token', methods=['POST'])
def api_push_token():
    d = request.get_json() or {}
    uid = d.get('usuario_id')
    token = (d.get('token') or '').strip()

    if not uid or not token:
        return jsonify({'ok': False}), 400

    nido_id = _nido()
    conn = get_db()

    existing = conn.execute(
        "SELECT id FROM fcm_tokens WHERE usuario_id=? AND token=?", (uid, token)
    ).fetchone()

    if existing:
        conn.execute(
            "UPDATE fcm_tokens SET nido_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (nido_id, existing['id'])
        )
    else:
        conn.execute(
            "INSERT INTO fcm_tokens (usuario_id, token, nido_id) VALUES (?, ?, ?)",
            (uid, token, nido_id)
        )

    conn.commit()
    conn.close()
    return jsonify({'ok': True})
