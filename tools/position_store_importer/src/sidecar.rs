//! TIQSIDE1 compact policy sidecar writer.

use std::fs::{self, File};
use std::io::{self, Seek, Write};
use std::path::{Path, PathBuf};

use flate2::write::GzEncoder;
use flate2::Compression;
use thiserror::Error;

pub const SIDECAR_MAGIC: &[u8; 8] = b"TIQSIDE1";
pub const SIDECAR_VERSION: u16 = 1;

/// Reference into a written sidecar policy record.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct SidecarRef {
    pub path: String,
    pub offset: u64,
    pub record_bytes: usize,
    pub policy_len: u8,
}

#[derive(Debug, Error)]
pub enum SidecarError {
    #[error("io: {0}")]
    Io(#[from] io::Error),
    #[error("policy length {0} exceeds u8 maximum")]
    PolicyTooLong(usize),
    #[error("canonical hash must be 32 bytes")]
    BadHashLength,
    #[error("sidecar writer already finalized")]
    Finalized,
}

/// Append-only TIQSIDE1 sidecar for one friend iteration shard.
pub struct SidecarWriter {
    rel_path: String,
    partial_path: PathBuf,
    final_path: PathBuf,
    encoder: Option<GzEncoder<File>>,
    header_written: bool,
}

impl SidecarWriter {
    /// Open (or create) `sidecar_dir/friend_selfplay/{iteration}.policy.bin.gz`.
    pub fn open(sidecar_dir: &Path, rel_root: &Path, iteration: &str) -> Result<Self, SidecarError> {
        let dir = sidecar_dir.join("friend_selfplay");
        fs::create_dir_all(&dir)?;
        let file_name = format!("{iteration}.policy.bin.gz");
        let final_path = dir.join(&file_name);
        let partial_path = dir.join(format!("{iteration}.policy.bin.gz.partial"));
        let rel = sidecar_dir
            .strip_prefix(rel_root)
            .unwrap_or(sidecar_dir)
            .join("friend_selfplay")
            .join(&file_name);
        let rel_path = rel.to_string_lossy().replace('\\', "/");
        Ok(Self {
            rel_path,
            partial_path,
            final_path,
            encoder: None,
            header_written: false,
        })
    }

    fn ensure_encoder(&mut self) -> Result<&mut GzEncoder<File>, SidecarError> {
        if self.encoder.is_none() {
            let file = fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(&self.partial_path)?;
            self.encoder = Some(GzEncoder::new(file, Compression::default()));
        }
        Ok(self.encoder.as_mut().expect("encoder just set"))
    }

    fn write_header_if_needed(&mut self) -> Result<(), SidecarError> {
        if self.header_written {
            return Ok(());
        }
        let encoder = self.ensure_encoder()?;
        encoder.write_all(SIDECAR_MAGIC)?;
        encoder.write_all(&SIDECAR_VERSION.to_le_bytes())?;
        self.header_written = true;
        Ok(())
    }

    /// Write one sparse policy record and return its sidecar reference.
    pub fn write_policy(
        &mut self,
        canonical_hash: &[u8; 32],
        move_codes: &[u8],
        policy_values: &[f64],
    ) -> Result<SidecarRef, SidecarError> {
        if move_codes.len() != policy_values.len() {
            return Err(SidecarError::Io(io::Error::new(
                io::ErrorKind::InvalidInput,
                "move_codes and policy_values length mismatch",
            )));
        }
        let n = move_codes.len();
        if n > u8::MAX as usize {
            return Err(SidecarError::PolicyTooLong(n));
        }

        self.write_header_if_needed()?;
        let encoder = self.ensure_encoder()?;
        let offset = {
            let file = encoder.get_mut();
            file.stream_position().map_err(SidecarError::Io)?
        };

        let mut record = Vec::with_capacity(1 + 32 + n * 3);
        record.push(n as u8);
        record.extend_from_slice(canonical_hash);
        for (&mv, &prob) in move_codes.iter().zip(policy_values.iter()) {
            record.push(mv);
            let q = quantize_prob(prob);
            record.extend_from_slice(&q.to_le_bytes());
        }

        encoder.write_all(&record)?;
        let record_bytes = record.len();

        Ok(SidecarRef {
            path: self.rel_path.clone(),
            offset,
            record_bytes,
            policy_len: n as u8,
        })
    }

    /// Finish gzip stream and atomically rename `.partial` → final path.
    pub fn finalize(mut self) -> Result<PathBuf, SidecarError> {
        if let Some(encoder) = self.encoder.take() {
            encoder.finish()?;
        } else if !self.header_written {
            // Empty sidecar: still write header-only gzip member for a valid file.
            self.write_header_if_needed()?;
            if let Some(encoder) = self.encoder.take() {
                encoder.finish()?;
            }
        }
        if self.partial_path.exists() {
            if self.final_path.exists() {
                fs::remove_file(&self.final_path)?;
            }
            fs::rename(&self.partial_path, &self.final_path)?;
        }
        Ok(self.final_path)
    }
}

fn quantize_prob(v: f64) -> u16 {
    let scaled = (v * 65535.0).round();
    if scaled <= 0.0 {
        0
    } else if scaled >= 65535.0 {
        65535
    } else {
        scaled as u16
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Read;
    use flate2::read::GzDecoder;
    use tempfile::tempdir;

    #[test]
    fn sidecar_header_and_record_layout() {
        let dir = tempdir().unwrap();
        let rel_root = dir.path().join("teacher_sidecars");
        let mut writer = SidecarWriter::open(dir.path(), &rel_root, "iter_000001").unwrap();
        let hash = [0xABu8; 32];
        let sidecar_ref = writer
            .write_policy(&hash, &[128, 129], &[0.5, 0.5])
            .unwrap();
        assert_eq!(sidecar_ref.policy_len, 2);
        assert_eq!(sidecar_ref.record_bytes, 1 + 32 + 2 * 3);
        assert!(sidecar_ref.path.ends_with("iter_000001.policy.bin.gz"));
        let final_path = writer.finalize().unwrap();
        assert!(final_path.exists());

        let compressed = fs::read(&final_path).unwrap();
        let mut decoder = GzDecoder::new(&compressed[..]);
        let mut raw = Vec::new();
        decoder.read_to_end(&mut raw).unwrap();
        assert_eq!(&raw[..8], SIDECAR_MAGIC);
        assert_eq!(u16::from_le_bytes([raw[8], raw[9]]), SIDECAR_VERSION);
        assert_eq!(raw[10], 2);
        assert_eq!(&raw[11..43], &hash);
        assert_eq!(raw[43], 128);
        assert_eq!(u16::from_le_bytes([raw[44], raw[45]]), quantize_prob(0.5));
    }
}
