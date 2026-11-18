//! gyrex-ingest: reads offers as JSON lines on stdin and prints one JSON summary on stdout.
//!
//!     DATABASE_URL=... gyrex-ingest --source gpuhunt < offers.ndjson
//!     gyrex-ingest --source gpuhunt --validate-only < offers.ndjson
//!
//! Exit codes: 0 finished, 1 failed (the summary says why), 2 bad usage.
//! The connection string is read from the environment only, so it never shows up in `ps`.

use std::io::stdin;
use std::process::ExitCode;

use gyrex_ingest::neon::{sql_endpoint, Http, Neon};
use gyrex_ingest::{run, Config, Db, Summary};

const USAGE: &str = "usage: gyrex-ingest --source NAME [--max-reject-pct N] [--validate-only]";
const CHUNK_SIZE: usize = 1000;
const DEFAULT_MAX_REJECT_PCT: f64 = 5.0;

struct Args {
    config: Config,
    validate_only: bool,
}

fn parse_args(mut args: impl Iterator<Item = String>) -> Result<Args, String> {
    let mut source = None;
    let mut max_reject_pct = DEFAULT_MAX_REJECT_PCT;
    let mut validate_only = false;
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--source" => source = Some(args.next().ok_or("--source needs a value")?),
            "--max-reject-pct" => {
                let value = args.next().ok_or("--max-reject-pct needs a value")?;
                max_reject_pct = value
                    .parse()
                    .map_err(|_| "--max-reject-pct must be a number")?;
                if !(0.0..=100.0).contains(&max_reject_pct) {
                    return Err("--max-reject-pct must be between 0 and 100".into());
                }
            }
            "--validate-only" => validate_only = true,
            other => return Err(format!("unknown argument: {other}")),
        }
    }
    let source = source
        .filter(|s| !s.is_empty())
        .ok_or("--source is required")?;
    Ok(Args {
        config: Config {
            source,
            chunk_size: CHUNK_SIZE,
            max_reject_pct,
        },
        validate_only,
    })
}

fn endpoint_for(database_url: &str) -> Result<String, String> {
    #[cfg(feature = "test-endpoint")]
    if let Ok(endpoint) = std::env::var("GYREX_TEST_SQL_ENDPOINT") {
        return Ok(endpoint);
    }
    sql_endpoint(database_url)
}

fn ingest(args: &Args) -> Summary {
    let input = stdin().lock();
    if args.validate_only {
        return run(&args.config, input, None);
    }
    let client = std::env::var("DATABASE_URL")
        .map_err(|_| "DATABASE_URL is not set".to_owned())
        .and_then(|url| {
            let endpoint = endpoint_for(&url)?;
            Ok(Neon::new(Http::new(endpoint, url.clone()), &url))
        });
    match client {
        Ok(neon) => run(&args.config, input, Some(&neon as &dyn Db)),
        Err(error) => Summary {
            source: args.config.source.clone(),
            error: Some(error),
            ..Summary::default()
        },
    }
}

fn main() -> ExitCode {
    let args = match parse_args(std::env::args().skip(1)) {
        Ok(args) => args,
        Err(error) => {
            eprintln!("{error}\n{USAGE}");
            return ExitCode::from(2);
        }
    };
    let summary = ingest(&args);
    match serde_json::to_string(&summary) {
        Ok(text) => println!("{text}"),
        Err(error) => {
            eprintln!("could not encode summary: {error}");
            return ExitCode::FAILURE;
        }
    }
    if summary.ok {
        ExitCode::SUCCESS
    } else {
        ExitCode::FAILURE
    }
}
