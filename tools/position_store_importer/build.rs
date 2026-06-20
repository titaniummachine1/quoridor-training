fn main() {
    // micropool + deep validation on Windows default 1MB main stack can overflow
    println!("cargo:rustc-link-arg=/STACK:8388608");
}
