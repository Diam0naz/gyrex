//! Per-row checks that mirror the `offers` table and `ingest_offers()`.
//!
//! Postgres applies a batch as one statement, so a single row it refuses fails the other 999.
//! Anything Postgres would refuse has to be caught here, one row at a time.

use serde_json::Value;

/// Largest value `numeric(12, 6)` can hold.
const MAX_PRICE: f64 = 999_999.999_999;

#[derive(Debug, Clone, PartialEq)]
pub struct Offer {
    pub key: String,
    pub provider: String,
    pub price: f64,
    pub row: Value,
}

fn text<'a>(row: &'a Value, field: &str) -> Option<&'a str> {
    row.get(field)?.as_str()
}

fn non_empty<'a>(row: &'a Value, field: &str) -> Option<&'a str> {
    text(row, field).filter(|s| !s.is_empty())
}

fn fits_i32(v: &Value) -> bool {
    v.as_i64().is_some_and(|n| i32::try_from(n).is_ok())
}

/// Postgres rejects U+0000 in both `text` and `jsonb`.
fn has_nul(v: &Value) -> bool {
    match v {
        Value::String(s) => s.contains('\0'),
        Value::Array(items) => items.iter().any(has_nul),
        Value::Object(map) => map.iter().any(|(k, v)| k.contains('\0') || has_nul(v)),
        _ => false,
    }
}

pub fn validate(row: Value, source: &str) -> Result<Offer, &'static str> {
    if !row.is_object() {
        return Err("row is not an object");
    }
    if text(&row, "source") != Some(source) {
        return Err("source does not match this run");
    }
    let provider = non_empty(&row, "provider").ok_or("missing provider")?;
    let instance = non_empty(&row, "instance_name").ok_or("missing instance_name")?;
    let region = text(&row, "region").ok_or("missing region")?;
    text(&row, "gpu_raw").ok_or("missing gpu_raw")?;

    let offer_type = text(&row, "offer_type").ok_or("missing offer_type")?;
    if !matches!(offer_type, "on-demand" | "spot" | "decentralized") {
        return Err("unknown offer_type");
    }

    let count = row
        .get("gpu_count")
        .filter(|v| fits_i32(v))
        .and_then(Value::as_i64);
    let count = count
        .filter(|n| *n > 0)
        .ok_or("gpu_count must be a positive integer")?;

    let price = row
        .get("price_usd_hr")
        .and_then(Value::as_f64)
        .ok_or("price is not a number")?;
    // A free offer is a data error here, and it would become the index minimum.
    if !(price > 0.0 && price <= MAX_PRICE) {
        return Err("price out of range");
    }

    match row.get("gpu_model") {
        None | Some(Value::Null | Value::String(_)) => {}
        _ => return Err("gpu_model is not text"),
    }
    match row.get("vram_gb") {
        None | Some(Value::Null) => {}
        Some(v) if v.as_f64().is_some_and(|n| n >= 0.0) => {}
        _ => return Err("vram_gb is not a non-negative number"),
    }
    match row.get("available") {
        None | Some(Value::Null) => {}
        Some(v) if fits_i32(v) => {}
        _ => return Err("available is not an integer"),
    }
    match row.get("extra") {
        None | Some(Value::Null | Value::Object(_)) => {}
        _ => return Err("extra is not an object"),
    }
    if has_nul(&row) {
        return Err("contains a NUL character");
    }

    let spot = if offer_type == "spot" { "spot" } else { "od" };
    let key = format!("{source}|{provider}|{instance}|{region}|{spot}|{count}");
    if text(&row, "offer_key") != Some(key.as_str()) {
        return Err("offer_key does not match the offer");
    }

    let provider = provider.to_owned();
    Ok(Offer {
        key,
        provider,
        price,
        row,
    })
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use serde_json::json;

    pub(crate) fn row(instance: &str, price: f64) -> Value {
        json!({
            "offer_key": format!("gpuhunt|aws|{instance}|us-east-1|od|1"),
            "source": "gpuhunt", "provider": "aws", "offer_type": "on-demand",
            "instance_name": instance, "region": "us-east-1", "gpu_raw": "H100",
            "gpu_model": "H100", "gpu_count": 1, "vram_gb": 80.0,
            "price_usd_hr": price, "available": null, "extra": {}
        })
    }

    fn with(field: &str, value: Value) -> Value {
        let mut r = row("a", 2.0);
        r[field] = value;
        r
    }

    #[test]
    fn accepts_a_good_row() {
        let offer = validate(row("a", 2.5), "gpuhunt").unwrap();
        assert_eq!(offer.key, "gpuhunt|aws|a|us-east-1|od|1");
        assert_eq!(offer.price, 2.5);
    }

    #[test]
    fn refuses_what_postgres_would_refuse() {
        let cases = [
            (with("price_usd_hr", json!(-1.0)), "price out of range"),
            (with("price_usd_hr", json!(0.0)), "price out of range"),
            (with("price_usd_hr", json!(1e7)), "price out of range"),
            (with("price_usd_hr", json!("2.0")), "price is not a number"),
            (
                with("gpu_count", json!(0)),
                "gpu_count must be a positive integer",
            ),
            (
                with("gpu_count", json!(1.5)),
                "gpu_count must be a positive integer",
            ),
            (
                with("gpu_count", json!(5_000_000_000_i64)),
                "gpu_count must be a positive integer",
            ),
            (with("offer_type", json!("reserved")), "unknown offer_type"),
            (with("available", json!(1.5)), "available is not an integer"),
            (
                with("vram_gb", json!(-8)),
                "vram_gb is not a non-negative number",
            ),
            (with("extra", json!([])), "extra is not an object"),
            (
                with("gpu_raw", json!("H\u{0}100")),
                "contains a NUL character",
            ),
            (
                with("extra", json!({"a": ["\u{0}"]})),
                "contains a NUL character",
            ),
            (
                with("offer_key", json!("other")),
                "offer_key does not match the offer",
            ),
            (
                with("source", json!("nosana")),
                "source does not match this run",
            ),
        ];
        for (bad, reason) in cases {
            assert_eq!(validate(bad, "gpuhunt").unwrap_err(), reason);
        }
    }

    #[test]
    fn spot_is_part_of_the_key_but_decentralized_is_not() {
        let mut spot = with("offer_type", json!("spot"));
        spot["offer_key"] = json!("gpuhunt|aws|a|us-east-1|spot|1");
        assert!(validate(spot, "gpuhunt").is_ok());
        assert!(validate(with("offer_type", json!("decentralized")), "gpuhunt").is_ok());
    }
}
