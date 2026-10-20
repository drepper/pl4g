// The back ends, by the triple each generates for.
#include "target/target.hh"

#include "target/registry.hh"


namespace pl4g::target {

  // Each architecture's own, defined beside it.
  std::unique_ptr<Target> make_x86_64(std::string_view triple);
  std::unique_ptr<Target> make_aarch64(std::string_view triple);
  std::unique_ptr<Target> make_riscv64(std::string_view triple);


  std::unique_ptr<Target> lookup_target(std::string_view triple)
  {
    auto architecture = backend_of(triple);
    if (! is_known_triple(triple)) [[unlikely]]
      return nullptr;
    if (architecture == "x86_64")
      return make_x86_64(triple);
    if (architecture == "aarch64")
      return make_aarch64(triple);
    if (architecture == "riscv64")
      return make_riscv64(triple);
    return nullptr;
  }

} // namespace pl4g::target
