pub mod aggregate;
pub mod db;
pub mod friend;
pub mod pipeline;
pub mod position_state;
pub mod progress;
pub mod sidecar;
pub mod staging;
pub mod staging_pipeline;

pub const IMPORTER_VERSION: &str = env!("CARGO_PKG_VERSION");
pub const MICROPOOL_CRATE_VERSION: &str = "0.3.0";
