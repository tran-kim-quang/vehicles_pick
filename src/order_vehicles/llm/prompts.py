EXTRACTION_SYSTEM = """Bạn trích xuất dữ liệu do chính khách vừa nói thành JSON.
Chỉ dùng transcript user, không lấy dữ liệu từ lời assistant/context. Schema:
{\"patch\":{\"name\":string|null,\"phone\":string|null,\"booking_mode\":\"now\"|\"scheduled\"|null,\"schedule\":{\"date\":\"YYYY-MM-DD\"|null,\"relative_days\":integer|null,\"hour\":integer|null,\"minute\":integer,\"period\":\"am\"|\"pm\"|\"24h\"|null}|null,\"vehicle_choice\":string|null},\"intent\":\"provide\"|\"correct\"|\"affirm\"|\"deny\"|\"cancel\"|\"unknown\"}.
Không thêm field khác."""


def extraction_messages(transcript: str, context: dict, retry: bool = False):
    suffix = " JSON trước sai schema; trả lại đúng schema." if retry else ""
    return [
        {"role": "system", "content": EXTRACTION_SYSTEM + suffix},
        {"role": "system", "content": f"Context hợp lệ: {context}"},
        {"role": "user", "content": transcript},
    ]
