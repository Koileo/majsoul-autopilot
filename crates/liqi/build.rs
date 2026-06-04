fn main() -> Result<(), Box<dyn std::error::Error>> {
    let protoc = protoc_bin_vendored::protoc_bin_path()?;
    std::env::set_var("PROTOC", protoc);

    let proto_dir = "../../majsoul/liqi_proto";
    prost_build::Config::new()
        .compile_protos(&[format!("{proto_dir}/liqi.proto")], &[proto_dir])?;

    println!("cargo:rerun-if-changed={proto_dir}/liqi.proto");
    Ok(())
}
