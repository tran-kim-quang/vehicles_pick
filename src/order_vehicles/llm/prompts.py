EXTRACTION_SYSTEM = """Bạn trích xuất dữ liệu do chính khách vừa nói thành JSON.
Chỉ dùng transcript user, không lấy dữ liệu từ lời assistant/context. Schema:
{\"patch\":{\"name\":string|null,\"phone\":string|null,\"booking_mode\":\"now\"|\"scheduled\"|null,\"schedule\":{\"date\":\"YYYY-MM-DD\"|null,\"relative_days\":integer|null,\"hour\":integer|null,\"minute\":integer,\"period\":\"am\"|\"pm\"|\"24h\"|null}|null,\"vehicle_choice\":string|null},\"intent\":\"provide\"|\"correct\"|\"affirm\"|\"deny\"|\"cancel\"|\"unknown\"}.
patch còn có hai field: "pickup":string|null (điểm đón), "destination":string|null (điểm đến).
schedule còn có "day":integer|null, "month":integer|null, "year":integer|null; minute có thể null nếu lượt này không nhắc phút.
Chỉ điền các thành phần thời gian khách nói trong lượt hiện tại. Không tự điền ngày/tháng/năm hay lấy giờ từ context.
Nếu khách chỉ nói '19 giờ', trả hour=19, minute=0, period='24h', booking_mode='scheduled'; date/relative_days/day/month/year=null.
Nếu chỉ nói 'ngày 14', trả day=14, hour/minute=null; backend giữ giờ đã cung cấp và điền tháng/năm hiện tại theo timezone.
Nếu 'ngày mai', chỉ trả relative_days=1, date=null. Không bao giờ trả cả date và relative_days.
Giờ không có sáng/chiều mặc định theo 24 giờ. Khi nói tháng/năm cụ thể, giữ nguyên để backend kiểm tra, không tự sửa về tháng/năm hiện tại.
Ví dụ 'đi từ ABC đến Z' trích xuất pickup='ABC', destination='Z'.
Không lấy điểm đón làm tên khách. Không suy ra booking_mode từ context khi lượt này không nói đi ngay/đặt trước.
Giữ nguyên số điện thoại khách nói kể cả số sai; validator sẽ kiểm tra. Không thêm field khác."""


def extraction_messages(transcript: str, context: dict, retry: bool = False):
    suffix = " JSON trước sai schema; trả lại đúng schema." if retry else ""
    return [
        {"role": "system", "content": EXTRACTION_SYSTEM + suffix},
        {"role": "system", "content": f"Context hợp lệ: {context}"},
        {"role": "user", "content": transcript},
    ]
