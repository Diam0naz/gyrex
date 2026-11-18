//! The collector's write path: newline-delimited offers in, one JSON summary out.
//!
//! Rows are checked one at a time, so a bad row costs that row rather than its whole batch.
//! Delisting is the one destructive step, so it only runs for providers whose offers all arrived.

pub mod neon;
pub mod validate;

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::io::BufRead;

use serde::Serialize;
use serde_json::{json, Value};

use validate::{validate, Offer};

const MAX_REJECT_SAMPLES: usize = 20;

pub trait Db {
    /// Runs one statement and returns its rows as JSON objects.
    fn query(&self, sql: &str, params: Vec<Value>) -> Result<Vec<Value>, String>;
}

pub struct Config {
    pub source: String,
    pub chunk_size: usize,
    pub max_reject_pct: f64,
}

#[derive(Debug, Serialize)]
pub struct Reject {
    pub line: usize,
    pub provider: Option<String>,
    pub offer_key: Option<String>,
    pub reason: String,
}

#[derive(Debug, Default, Serialize)]
pub struct Summary {
    pub ok: bool,
    pub error: Option<String>,
    pub source: String,
    pub started_at: Option<String>,
    pub received: usize,
    pub valid: usize,
    pub rejected: usize,
    pub duplicates: usize,
    pub upserted: u64,
    pub price_changes: u64,
    pub deactivated: u64,
    pub closed_providers: Vec<String>,
    pub held_providers: Vec<String>,
    pub reject_reasons: BTreeMap<String, usize>,
    pub reject_samples: Vec<Reject>,
}

impl Summary {
    fn fail(mut self, error: impl Into<String>) -> Self {
        self.ok = false;
        self.error = Some(error.into());
        self
    }
}

/// Neon's driver sends arrays as Postgres array literals.
fn text_array(items: &[String]) -> String {
    let quoted: Vec<String> = items
        .iter()
        .map(|s| format!("\"{}\"", s.replace('\\', "\\\\").replace('"', "\\\"")))
        .collect();
    format!("{{{}}}", quoted.join(","))
}

fn count(v: Option<&Value>) -> Option<u64> {
    match v? {
        Value::Number(n) => n.as_u64(),
        Value::String(s) => s.parse().ok(),
        _ => None,
    }
}

fn first_cell<'a>(rows: &'a [Value], column: &str) -> Result<&'a Value, String> {
    rows.first()
        .and_then(|r| r.get(column))
        .ok_or_else(|| format!("query returned no {column}"))
}

fn write_chunk(db: &dyn Db, source: &str, chunk: &[Offer]) -> Result<(u64, u64), String> {
    let rows: Vec<&Value> = chunk.iter().map(|o| &o.row).collect();
    let payload = serde_json::to_string(&rows).map_err(|e| e.to_string())?;
    let result = db.query(
        "select ingest_offers($1, $2::jsonb) as r",
        vec![json!(source), json!(payload)],
    )?;
    let cell = first_cell(&result, "r")?;
    let parsed;
    let cell = match cell {
        Value::String(s) => {
            parsed = serde_json::from_str::<Value>(s).map_err(|e| e.to_string())?;
            &parsed
        }
        other => other,
    };
    let field =
        |name| count(cell.get(name)).ok_or_else(|| format!("ingest_offers returned no {name}"));
    Ok((field("upserted")?, field("price_changes")?))
}

/// Cheapest offer per key. A repeated key inside one batch is a hard Postgres error.
fn dedupe(offers: Vec<Offer>) -> Vec<Offer> {
    let mut seen: HashMap<String, usize> = HashMap::with_capacity(offers.len());
    let mut out: Vec<Offer> = Vec::with_capacity(offers.len());
    for offer in offers {
        match seen.get(&offer.key) {
            Some(&i) if offer.price < out[i].price => out[i] = offer,
            Some(_) => {}
            None => {
                seen.insert(offer.key.clone(), out.len());
                out.push(offer);
            }
        }
    }
    out
}

pub fn run(config: &Config, input: impl BufRead, db: Option<&dyn Db>) -> Summary {
    let mut s = Summary {
        source: config.source.clone(),
        ..Summary::default()
    };
    let mut offers = Vec::new();
    let mut held = BTreeSet::new();
    let mut hold_everything = false;

    for (index, line) in input.lines().enumerate() {
        let line = match line {
            Ok(line) => line,
            Err(e) => return s.fail(format!("could not read offers: {e}")),
        };
        if line.trim().is_empty() {
            continue;
        }
        s.received += 1;
        let parsed = serde_json::from_str::<Value>(&line);
        let (provider, offer_key) = match &parsed {
            Ok(row) => (
                row.get("provider")
                    .and_then(Value::as_str)
                    .filter(|p| !p.is_empty())
                    .map(str::to_owned),
                row.get("offer_key")
                    .and_then(Value::as_str)
                    .map(str::to_owned),
            ),
            Err(_) => (None, None),
        };
        let reason = match parsed {
            Ok(row) => match validate(row, &config.source) {
                Ok(offer) => {
                    offers.push(offer);
                    continue;
                }
                Err(reason) => reason,
            },
            Err(_) => "not valid JSON (NaN or Infinity?)",
        };
        s.rejected += 1;
        *s.reject_reasons.entry(reason.to_owned()).or_default() += 1;
        // A provider with a rejected row has an incomplete list: delisting from it could retire
        // offers that are still on sale. With no provider to blame, nothing can be delisted.
        match &provider {
            Some(p) => {
                held.insert(p.clone());
            }
            None => hold_everything = true,
        }
        if s.reject_samples.len() < MAX_REJECT_SAMPLES {
            s.reject_samples.push(Reject {
                line: index + 1,
                provider,
                offer_key,
                reason: reason.to_owned(),
            });
        }
    }

    let before = offers.len();
    let offers = dedupe(offers);
    s.valid = offers.len();
    s.duplicates = before - offers.len();

    let providers: BTreeSet<String> = offers.iter().map(|o| o.provider.clone()).collect();
    let (held, closable): (Vec<String>, Vec<String>) = providers
        .into_iter()
        .partition(|p| hold_everything || held.contains(p));
    s.held_providers = held;

    // Many bad rows means the input format changed, so none of it is trustworthy.
    if s.received > 0 && s.rejected as f64 * 100.0 / s.received as f64 > config.max_reject_pct {
        let message = format!(
            "{} of {} rows rejected, over the {}% limit; nothing written",
            s.rejected, s.received, config.max_reject_pct
        );
        return s.fail(message);
    }

    let Some(db) = db else {
        s.closed_providers = closable;
        s.ok = true;
        return s;
    };
    if offers.is_empty() {
        s.ok = true;
        return s;
    }

    // Taken before the first write: every row written below gets last_seen >= started_at.
    let started_at = db
        .query("select now()::text as t", vec![])
        .and_then(|rows| {
            first_cell(&rows, "t")?
                .as_str()
                .map(str::to_owned)
                .ok_or_else(|| "bad clock".to_owned())
        });
    let started_at = match started_at {
        Ok(t) => t,
        Err(e) => return s.fail(e),
    };
    s.started_at = Some(started_at.clone());

    for chunk in offers.chunks(config.chunk_size) {
        match write_chunk(db, &config.source, chunk) {
            Ok((upserted, changes)) => {
                s.upserted += upserted;
                s.price_changes += changes;
            }
            // Nothing is delisted after a failed write: unwritten offers would look gone.
            Err(e) => return s.fail(e),
        }
    }

    if !closable.is_empty() {
        let params = vec![
            json!(config.source),
            json!(text_array(&closable)),
            json!(started_at),
        ];
        let closed = db
            .query(
                "select close_run($1, $2::text[], $3::timestamptz) as n",
                params,
            )
            .and_then(|rows| {
                count(Some(first_cell(&rows, "n")?))
                    .ok_or_else(|| "bad close_run result".to_owned())
            });
        match closed {
            Ok(n) => s.deactivated = n,
            Err(e) => return s.fail(e),
        }
    }
    s.closed_providers = closable;
    s.ok = true;
    s
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::validate::tests::row;
    use std::cell::RefCell;

    #[derive(Default)]
    struct FakeDb {
        calls: RefCell<Vec<(String, Vec<Value>)>>,
        fail_on_write: Option<usize>,
    }

    impl Db for FakeDb {
        fn query(&self, sql: &str, params: Vec<Value>) -> Result<Vec<Value>, String> {
            let mut calls = self.calls.borrow_mut();
            calls.push((sql.to_owned(), params.clone()));
            if sql.contains("now()") {
                return Ok(vec![json!({"t": "2026-11-18 00:00:00+00"})]);
            }
            if sql.contains("close_run") {
                return Ok(vec![json!({"n": 2})]);
            }
            let writes = calls
                .iter()
                .filter(|(q, _)| q.contains("ingest_offers"))
                .count();
            if self.fail_on_write == Some(writes) {
                return Err("boom".into());
            }
            let n = serde_json::from_str::<Vec<Value>>(params[1].as_str().unwrap())
                .unwrap()
                .len();
            Ok(vec![json!({"r": {"upserted": n, "price_changes": 1}})])
        }
    }

    impl FakeDb {
        fn closed(&self) -> Vec<Vec<Value>> {
            let calls = self.calls.borrow();
            calls
                .iter()
                .filter(|(q, _)| q.contains("close_run"))
                .map(|(_, p)| p.clone())
                .collect()
        }
        fn writes(&self) -> usize {
            self.calls
                .borrow()
                .iter()
                .filter(|(q, _)| q.contains("ingest_offers"))
                .count()
        }
    }

    fn config() -> Config {
        Config {
            source: "gpuhunt".into(),
            chunk_size: 2,
            max_reject_pct: 50.0,
        }
    }

    fn lines(rows: &[Value]) -> String {
        rows.iter()
            .map(Value::to_string)
            .collect::<Vec<_>>()
            .join("\n")
    }

    fn other_provider(instance: &str) -> Value {
        let mut r = row(instance, 3.0);
        r["provider"] = json!("gcp");
        r["offer_key"] = json!(format!("gpuhunt|gcp|{instance}|us-east-1|od|1"));
        r
    }

    #[test]
    fn writes_in_chunks_then_closes_the_run() {
        let db = FakeDb::default();
        let input = lines(&[row("a", 1.0), row("b", 1.0), row("c", 1.0)]);
        let s = run(&config(), input.as_bytes(), Some(&db));
        assert!(s.ok, "{:?}", s.error);
        assert_eq!((s.upserted, s.price_changes, s.deactivated), (3, 2, 2));
        assert_eq!(db.writes(), 2);
        assert_eq!(db.closed()[0][1], json!("{\"aws\"}"));
        assert_eq!(db.closed()[0][2], json!("2026-11-18 00:00:00+00"));
    }

    #[test]
    fn a_bad_row_costs_one_row_and_holds_back_only_its_provider() {
        let db = FakeDb::default();
        let input = lines(&[row("a", 1.0), row("b", -1.0), other_provider("c")]);
        let s = run(&config(), input.as_bytes(), Some(&db));
        assert!(s.ok);
        assert_eq!((s.valid, s.rejected, s.upserted), (2, 1, 2));
        assert_eq!(s.held_providers, ["aws"]);
        assert_eq!(s.closed_providers, ["gcp"]);
        assert_eq!(db.closed()[0][1], json!("{\"gcp\"}"));
    }

    #[test]
    fn an_unattributable_row_blocks_all_delisting() {
        let db = FakeDb::default();
        let input = format!("{}\n{{\"price_usd_hr\": NaN}}\n", row("a", 1.0));
        let s = run(&config(), input.as_bytes(), Some(&db));
        assert!(s.ok);
        assert_eq!(s.upserted, 1);
        assert_eq!(s.held_providers, ["aws"]);
        assert!(db.closed().is_empty());
    }

    #[test]
    fn too_many_rejects_write_nothing() {
        let db = FakeDb::default();
        let input = lines(&[row("a", -1.0), row("b", -1.0), row("c", 1.0)]);
        let s = run(&config(), input.as_bytes(), Some(&db));
        assert!(!s.ok);
        assert!(db.calls.borrow().is_empty());
    }

    #[test]
    fn a_failed_write_never_delists() {
        let db = FakeDb {
            fail_on_write: Some(2),
            ..FakeDb::default()
        };
        let input = lines(&[row("a", 1.0), row("b", 1.0), row("c", 1.0)]);
        let s = run(&config(), input.as_bytes(), Some(&db));
        assert!(!s.ok);
        assert_eq!(s.error.as_deref(), Some("boom"));
        assert_eq!(s.upserted, 2);
        assert!(db.closed().is_empty());
    }

    #[test]
    fn duplicates_keep_the_cheapest_offer() {
        let db = FakeDb::default();
        let input = lines(&[row("a", 3.0), row("a", 1.0), row("a", 2.0)]);
        let s = run(&config(), input.as_bytes(), Some(&db));
        assert_eq!((s.valid, s.duplicates), (1, 2));
        let calls = db.calls.borrow();
        let (_, params) = calls
            .iter()
            .find(|(q, _)| q.contains("ingest_offers"))
            .unwrap();
        let sent: Vec<Value> = serde_json::from_str(params[1].as_str().unwrap()).unwrap();
        assert_eq!(sent[0]["price_usd_hr"], json!(1.0));
    }

    #[test]
    fn validate_only_touches_no_database() {
        let s = run(&config(), lines(&[row("a", 1.0)]).as_bytes(), None);
        assert!(s.ok);
        assert_eq!((s.valid, s.upserted), (1, 0));
    }

    #[test]
    fn array_literals_are_escaped() {
        assert_eq!(
            text_array(&["a\"b".into(), "c\\d".into()]),
            r#"{"a\"b","c\\d"}"#
        );
    }
}
