import math

import pandas as pd


GROUPS = [
    "OCEAN FREIGHT",
    "ORIGIN LOCAL CHARGES",
    "DESTINATION LOCAL CHARGES",
    "CHI PHÍ NỘI BỘ",
    "CHƯA PHÂN LOẠI",
]

FEE_COLUMNS = [
    "Nhóm phí",
    "Khoản phí",
    "Đơn vị",
    "Số lượng",
    "Giá mua (USD)",
    "Profit (USD)",
    "Giá bán (USD)",
    "Ghi chú",
    "Hiển thị PDF",
]


def calculate_quote(fee_rows):
    fees = fee_rows.copy()
    if "Profit (USD)" not in fees.columns:
        if {"Giá bán (USD)", "Giá mua (USD)"}.issubset(fees.columns):
            purchase = pd.to_numeric(fees["Giá mua (USD)"], errors="coerce")
            selling = pd.to_numeric(fees["Giá bán (USD)"], errors="coerce")
            fees["Profit (USD)"] = selling.fillna(0) - purchase.fillna(0)
        else:
            fees["Profit (USD)"] = 0.0
    defaults = {
        "Nhóm phí": "",
        "Khoản phí": "",
        "Đơn vị": "",
        "Số lượng": 1.0,
        "Giá mua (USD)": 0.0,
        "Profit (USD)": 0.0,
        "Giá bán (USD)": 0.0,
        "Ghi chú": "",
        "Hiển thị PDF": False,
    }
    for column, default in defaults.items():
        if column not in fees.columns:
            fees[column] = default

    for column in ("Số lượng", "Giá mua (USD)", "Profit (USD)"):
        fees[column] = pd.to_numeric(fees[column], errors="coerce")
    for column in ("Nhóm phí", "Khoản phí", "Đơn vị", "Ghi chú"):
        fees[column] = fees[column].fillna("").astype(str)

    internal = (
        fees["Nhóm phí"].eq("CHI PHÍ NỘI BỘ")
        | fees["Khoản phí"].str.contains(
            r"commission|internal", case=False, regex=True, na=False
        )
    )
    unclassified = fees["Nhóm phí"].eq("CHƯA PHÂN LOẠI")

    fees.loc[internal, "Nhóm phí"] = "CHI PHÍ NỘI BỘ"
    fees.loc[internal, "Hiển thị PDF"] = False
    fees.loc[internal, "Profit (USD)"] = -fees.loc[internal, "Giá mua (USD)"]
    fees["Giá bán (USD)"] = fees["Giá mua (USD)"] + fees["Profit (USD)"]
    fees.loc[internal, "Giá bán (USD)"] = 0.0
    fees["Thành tiền mua"] = fees["Số lượng"] * fees["Giá mua (USD)"]
    fees["Profit thành tiền"] = fees["Số lượng"] * fees["Profit (USD)"]
    fees["Thành tiền bán"] = fees["Số lượng"] * fees["Giá bán (USD)"]

    public_mask = (
        fees["Hiển thị PDF"].fillna(False).astype(bool)
        & ~internal
        & ~unclassified
        & fees["Khoản phí"].str.strip().ne("")
    )
    public = fees.loc[public_mask].copy()
    total_cost = float(fees["Thành tiền mua"].sum())
    total_sell = float(public["Thành tiền bán"].sum())
    net_profit = total_sell - total_cost
    margin = net_profit / total_sell * 100 if total_sell > 0 else 0.0

    return fees, public, {
        "total_cost": total_cost,
        "total_sell": total_sell,
        "net_profit": net_profit,
        "margin": margin,
    }


def internal_fee_mask(fee_rows):
    return (
        fee_rows["Nhóm phí"].eq("CHI PHÍ NỘI BỘ")
        | fee_rows["Khoản phí"].str.contains(
            r"commission|internal", case=False, regex=True, na=False
        )
    )


def allocate_profit_by_cost(fee_rows, total_profit):
    fees = fee_rows.copy()
    try:
        total_profit = float(total_profit)
    except (TypeError, ValueError) as error:
        raise ValueError("Tổng Profit phải là một số không âm.") from error
    if not math.isfinite(total_profit) or total_profit < 0:
        raise ValueError("Tổng Profit phải là một số không âm.")

    required = ("Nhóm phí", "Khoản phí", "Số lượng", "Giá mua (USD)")
    for column in required:
        if column not in fees.columns:
            fees[column] = "" if column in required[:2] else None
    if "Profit (USD)" not in fees.columns:
        fees["Profit (USD)"] = 0.0

    eligible = (
        ~internal_fee_mask(fees)
        & fees["Khoản phí"].fillna("").astype(str).str.strip().ne("")
    )
    if not eligible.any():
        if total_profit > 0:
            raise ValueError(
                "Thêm khoản phí có giá mua và số lượng trước khi phân bổ Profit."
            )
        return fees

    fees["Số lượng"] = pd.to_numeric(fees["Số lượng"], errors="coerce")
    fees["Giá mua (USD)"] = pd.to_numeric(
        fees["Giá mua (USD)"], errors="coerce"
    )
    if total_profit == 0:
        fees.loc[eligible, "Profit (USD)"] = 0.0
        return fees

    quantities = fees.loc[eligible, "Số lượng"]
    unit_costs = fees.loc[eligible, "Giá mua (USD)"]
    if (
        quantities.isna().any()
        or quantities.le(0).any()
        or unit_costs.isna().any()
        or unit_costs.lt(0).any()
    ):
        raise ValueError(
            "Để phân bổ Profit theo tỷ trọng, hãy nhập số lượng hợp lệ và "
            "giá mua cho mọi khoản phí."
        )

    total_cost = float((quantities * unit_costs).sum())
    if total_cost <= 0:
        raise ValueError(
            "Không thể phân bổ theo tỷ trọng vì tổng giá mua các khoản phí bằng 0."
        )

    fees.loc[eligible, "Profit (USD)"] = (
        total_profit * unit_costs / total_cost
    ).round(2)
    return fees


def validate_pdf(fields, public_rows, fee_rows):
    errors = []
    if not str(fields.get("quote_customer", "")).strip():
        errors.append("Thiếu tên khách hàng.")
    if not str(fields.get("quote_number", "")).strip():
        errors.append("Thiếu mã báo giá.")
    if (
        not str(fields.get("quote_pol", "")).strip()
        or not str(fields.get("quote_pod", "")).strip()
    ):
        errors.append("Thiếu POL hoặc POD.")
    if not fields.get("quote_incoterm"):
        errors.append("Thiếu Incoterm.")
    if not fields.get("quote_container"):
        errors.append("Thiếu loại container.")
    container_quantity = pd.to_numeric(
        pd.Series([fields.get("quote_qty")]), errors="coerce"
    ).iloc[0]
    if pd.isna(container_quantity) or container_quantity <= 0:
        errors.append("Thiếu số container hợp lệ.")
    valid_until = fields.get("quote_valid_until")
    if valid_until is None:
        errors.append("Thiếu ngày hết hiệu lực.")
    elif valid_until < fields["quote_date"]:
        errors.append("Ngày hết hiệu lực sớm hơn ngày báo giá.")
    if public_rows.empty:
        errors.append("Chưa có khoản phí công khai.")
    unresolved = (
        ~fee_rows["Nhóm phí"].isin(GROUPS)
        | fee_rows["Nhóm phí"].eq("CHƯA PHÂN LOẠI")
    ) & fee_rows["Khoản phí"].str.strip().ne("")
    if unresolved.any():
        errors.append(
            "Còn phí chưa phân loại hoặc thiếu nhóm; hãy phân loại hoặc xóa khoản không áp dụng."
        )
    if public_rows["Giá mua (USD)"].isna().any():
        errors.append("Cần xác nhận giá mua cho mọi khoản phí công khai.")
    if public_rows["Profit (USD)"].isna().any():
        errors.append("Cần nhập Profit cho mọi khoản phí công khai.")
    if public_rows["Giá bán (USD)"].isna().any():
        errors.append("Giá bán chưa được tính cho mọi khoản phí công khai.")
    if public_rows["Giá bán (USD)"].lt(0).any():
        errors.append("Giá bán không được âm.")
    if public_rows["Giá mua (USD)"].lt(0).any():
        errors.append("Giá mua không được âm.")
    quantities = pd.to_numeric(public_rows["Số lượng"], errors="coerce")
    if quantities.isna().any() or quantities.le(0).any():
        errors.append("Số lượng phí công khai phải lớn hơn 0.")
    units = public_rows["Đơn vị"].fillna("").astype(str).str.strip().str.upper()
    if units.isin(("", "USD", "US$", "CHƯA RÕ")).any():
        errors.append("Cần xác nhận đơn vị tính phí cho mọi dòng công khai.")
    return errors
