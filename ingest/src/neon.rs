//! Client for Neon's SQL-over-HTTP endpoint, with retries and credential redaction.

use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};
use url::Url;

use crate::Db;

const ATTEMPTS: u32 = 5;
const REQUEST_TIMEOUT: Duration = Duration::from_secs(120);
const MAX_RESPONSE_BYTES: u64 = 16 * 1024 * 1024;
const MAX_ERROR_CHARS: usize = 500;

pub trait Transport {
    /// Returns (HTTP status, body), or an error when no response arrived.
    fn post(&self, body: &str) -> Result<(u16, String), String>;
}

pub struct Http {
    agent: ureq::Agent,
    endpoint: String,
    connection_string: String,
}

impl Http {
    pub fn new(endpoint: String, connection_string: String) -> Self {
        let agent = ureq::Agent::config_builder()
            .timeout_global(Some(REQUEST_TIMEOUT))
            .http_status_as_error(false)
            .build()
            .into();
        Self {
            agent,
            endpoint,
            connection_string,
        }
    }
}

impl Transport for Http {
    fn post(&self, body: &str) -> Result<(u16, String), String> {
        let mut response = self
            .agent
            .post(&self.endpoint)
            .header("Neon-Connection-String", &self.connection_string)
            .header("Content-Type", "application/json")
            .send(body)
            .map_err(|e| e.to_string())?;
        let status = response.status().as_u16();
        let text = response
            .body_mut()
            .with_config()
            .limit(MAX_RESPONSE_BYTES)
            .read_to_string()
            .map_err(|e| e.to_string())?;
        Ok((status, text))
    }
}

/// postgresql://u:p@ep-x-pooler.c-8.us-east-2.aws.neon.tech/db
///   -> https://api.c-8.us-east-2.aws.neon.tech/sql
///
/// The connection string travels in a header, so this decides who receives the password.
pub fn sql_endpoint(database_url: &str) -> Result<String, String> {
    let url = Url::parse(database_url).map_err(|_| "DATABASE_URL is not a valid URL".to_owned())?;
    if !matches!(url.scheme(), "postgres" | "postgresql") {
        return Err("DATABASE_URL is not a Postgres URL".into());
    }
    let host = url.host_str().unwrap_or_default();
    if !host.ends_with(".neon.tech") {
        return Err("DATABASE_URL is not a Neon connection string".into());
    }
    let (_, rest) = host
        .split_once('.')
        .ok_or("DATABASE_URL has no usable host")?;
    Ok(format!("https://api.{rest}/sql"))
}

fn secrets_of(database_url: &str) -> Vec<String> {
    let mut secrets = vec![database_url.to_owned()];
    if let Some(password) = Url::parse(database_url)
        .ok()
        .and_then(|u| u.password().map(str::to_owned))
    {
        secrets.push(password.clone());
        if let Some(decoded) = percent_decode(&password) {
            secrets.push(decoded);
        }
    }
    secrets.retain(|s| !s.is_empty());
    secrets.dedup();
    secrets
}

fn percent_decode(s: &str) -> Option<String> {
    let bytes = s.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' {
            let hex = s.get(i + 1..i + 3)?;
            out.push(u8::from_str_radix(hex, 16).ok()?);
            i += 3;
        } else {
            out.push(bytes[i]);
            i += 1;
        }
    }
    String::from_utf8(out).ok()
}

pub struct Neon<T> {
    transport: T,
    secrets: Vec<String>,
    sleep: fn(Duration),
}

impl<T: Transport> Neon<T> {
    pub fn new(transport: T, database_url: &str) -> Self {
        Self {
            transport,
            secrets: secrets_of(database_url),
            sleep: std::thread::sleep,
        }
    }

    fn clean(&self, message: &str) -> String {
        let mut out = message.to_owned();
        for secret in &self.secrets {
            out = out.replace(secret, "[redacted]");
        }
        out.chars().take(MAX_ERROR_CHARS).collect()
    }

    fn backoff(&self, attempt: u32) {
        // Up to 250 ms of jitter; the clock is random enough to spread a handful of retries.
        let jitter = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_or(0, |d| d.subsec_millis() % 250);
        (self.sleep)(Duration::from_secs(1 << attempt) + Duration::from_millis(jitter.into()));
    }
}

fn error_message(body: &str) -> String {
    serde_json::from_str::<Value>(body)
        .ok()
        .and_then(|v| v.get("message")?.as_str().map(str::to_owned))
        .unwrap_or_else(|| body.to_owned())
}

impl<T: Transport> Db for Neon<T> {
    /// Retries cover a response lost after commit too: every statement sent here is idempotent.
    fn query(&self, sql: &str, params: Vec<Value>) -> Result<Vec<Value>, String> {
        let body = json!({ "query": sql, "params": params }).to_string();
        let mut last_error = String::new();
        for attempt in 0..ATTEMPTS {
            if attempt > 0 {
                self.backoff(attempt - 1);
            }
            match self.transport.post(&body) {
                Ok((status, text)) if (200..300).contains(&status) => {
                    let parsed: Value = serde_json::from_str(&text)
                        .map_err(|_| "Neon returned a response that is not JSON".to_owned())?;
                    return match parsed.get("rows") {
                        Some(Value::Array(rows)) => Ok(rows.clone()),
                        _ => Err("Neon response has no rows".to_owned()),
                    };
                }
                // 5xx: often a compute waking from scale-to-zero.
                Ok((status, text)) if status == 429 || status >= 500 => {
                    last_error = format!("{status}: {}", error_message(&text));
                }
                Ok((status, text)) => {
                    return Err(self.clean(&format!("{status}: {}", error_message(&text))));
                }
                Err(e) => last_error = e,
            }
        }
        Err(self.clean(&format!("gave up after {ATTEMPTS} attempts: {last_error}")))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;
    use std::collections::VecDeque;

    const URL: &str =
        "postgresql://u:s3cr%2Ft@ep-x-1-pooler.c-8.us-east-2.aws.neon.tech/db?sslmode=require";

    struct Script(
        RefCell<VecDeque<Result<(u16, String), String>>>,
        RefCell<u32>,
    );

    impl Transport for &Script {
        fn post(&self, _body: &str) -> Result<(u16, String), String> {
            *self.1.borrow_mut() += 1;
            self.0.borrow_mut().pop_front().expect("unexpected request")
        }
    }

    fn script(steps: Vec<Result<(u16, &str), &str>>) -> Script {
        let steps = steps
            .into_iter()
            .map(|s| s.map(|(c, b)| (c, b.to_owned())).map_err(str::to_owned))
            .collect();
        Script(RefCell::new(steps), RefCell::new(0))
    }

    fn client(s: &Script) -> Neon<&Script> {
        let mut neon = Neon::new(s, URL);
        neon.sleep = |_| {};
        neon
    }

    #[test]
    fn endpoint_only_ever_points_at_neon() {
        assert_eq!(
            sql_endpoint(URL).unwrap(),
            "https://api.c-8.us-east-2.aws.neon.tech/sql"
        );
        for bad in [
            "postgresql://u:p@localhost/db",
            "postgresql://u:p@neon.tech.evil.example/db",
            "https://u:p@ep-x.us-east-2.aws.neon.tech/db",
            "not a url",
        ] {
            assert!(sql_endpoint(bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn retries_cold_starts_rate_limits_and_lost_connections() {
        let s = script(vec![
            Ok((503, "waking")),
            Err("connection reset"),
            Ok((429, "slow down")),
            Ok((200, r#"{"rows":[{"n":1}]}"#)),
        ]);
        assert_eq!(
            client(&s).query("q", vec![]).unwrap(),
            vec![json!({"n": 1})]
        );
        assert_eq!(*s.1.borrow(), 4);
    }

    #[test]
    fn sql_errors_are_not_retried() {
        let s = script(vec![Ok((400, r#"{"message":"syntax error"}"#))]);
        assert_eq!(
            client(&s).query("q", vec![]).unwrap_err(),
            "400: syntax error"
        );
        assert_eq!(*s.1.borrow(), 1);
    }

    #[test]
    fn gives_up_after_the_last_attempt() {
        let s = script(vec![
            Ok((500, "a")),
            Ok((500, "b")),
            Ok((500, "c")),
            Ok((500, "d")),
            Ok((500, "e")),
        ]);
        assert_eq!(
            client(&s).query("q", vec![]).unwrap_err(),
            "gave up after 5 attempts: 500: e"
        );
    }

    #[test]
    fn errors_never_carry_credentials() {
        let message = format!(r#"{{"message":"bad {URL} s3cr/t s3cr%2Ft"}}"#);
        let s = script(vec![Ok((400, message.as_str()))]);
        let error = client(&s).query("q", vec![]).unwrap_err();
        assert!(!error.contains("s3cr"), "{error}");
    }
}
