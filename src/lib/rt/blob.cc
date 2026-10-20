// The runtime built for each architecture, by name.
#include "rt/blob.hh"


namespace pl4g::rt {

  extern const Blob aarch64_blob;
  extern const Blob riscv64_blob;
  extern const Blob x86_64_blob;


  const Blob* blob_for(std::string_view architecture) noexcept
  {
    if (architecture == "x86_64")
      return &x86_64_blob;
    if (architecture == "aarch64")
      return &aarch64_blob;
    if (architecture == "riscv64")
      return &riscv64_blob;
    return nullptr;
  }

} // namespace pl4g::rt
