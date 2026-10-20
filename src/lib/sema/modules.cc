// Finding modules, reading them once, and deciding what to call them.
#include "sema/modules.hh"

#include <algorithm>
#include <array>
#include <bit>
#include <cstdint>
#include <cstring>
#include <format>
#include <ranges>
#include <span>


namespace pl4g::sema {

  namespace {

    namespace fs = std::filesystem;


    // BLAKE2b (RFC 7693), unkeyed, with a digest as short as asked: what
    // Python's `hashlib.blake2b(data, digest_size=n)` answers.
    class Blake2b {
    public:
      explicit Blake2b(std::size_t digest) : digest(digest)
      {
        h = iv;
        h[0] ^= 0x01010000u ^ digest;
      }

      void update(std::span<const unsigned char> data)
      {
        for (auto byte : data) {
          if (filled == block.size()) {
            counted += block.size();
            compress(false);
            filled = 0;
          }
          block[filled++] = byte;
        }
      }

      std::vector<unsigned char> finish()
      {
        counted += filled;
        std::fill(block.begin() + filled, block.end(), 0);
        compress(true);
        std::vector<unsigned char> out(digest);
        for (std::size_t i = 0; i < digest; ++i)
          out[i] = static_cast<unsigned char>(h[i / 8] >> (8 * (i % 8)));
        return out;
      }

    private:
      static constexpr std::array<std::uint64_t, 8> iv = {
        0x6a09e667f3bcc908ull, 0xbb67ae8584caa73bull, 0x3c6ef372fe94f82bull, 0xa54ff53a5f1d36f1ull,
        0x510e527fade682d1ull, 0x9b05688c2b3e6c1full, 0x1f83d9abfb41bd6bull, 0x5be0cd19137e2179ull,
      };

      static constexpr std::uint8_t sigma[12][16] = {
        { 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15 },
        { 14, 10, 4, 8, 9, 15, 13, 6, 1, 12, 0, 2, 11, 7, 5, 3 },
        { 11, 8, 12, 0, 5, 2, 15, 13, 10, 14, 3, 6, 7, 1, 9, 4 },
        { 7, 9, 3, 1, 13, 12, 11, 14, 2, 6, 5, 10, 4, 0, 15, 8 },
        { 9, 0, 5, 7, 2, 4, 10, 15, 14, 1, 11, 12, 6, 8, 3, 13 },
        { 2, 12, 6, 10, 0, 11, 8, 3, 4, 13, 7, 5, 15, 14, 1, 9 },
        { 12, 5, 1, 15, 14, 13, 4, 10, 0, 7, 6, 3, 9, 2, 8, 11 },
        { 13, 11, 7, 14, 12, 1, 3, 9, 5, 0, 15, 4, 8, 6, 2, 10 },
        { 6, 15, 14, 9, 11, 3, 0, 8, 12, 2, 13, 7, 1, 4, 10, 5 },
        { 10, 2, 8, 4, 7, 6, 1, 5, 15, 11, 9, 14, 3, 12, 13, 0 },
        { 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15 },
        { 14, 10, 4, 8, 9, 15, 13, 6, 1, 12, 0, 2, 11, 7, 5, 3 },
      };

      void compress(bool last)
      {
        std::array<std::uint64_t, 16> m;
        for (std::size_t i = 0; i < 16; ++i) {
          std::uint64_t word = 0;
          for (std::size_t b = 0; b < 8; ++b)
            word |= std::uint64_t(block[8 * i + b]) << (8 * b);
          m[i] = word;
        }
        std::array<std::uint64_t, 16> v;
        std::copy(h.begin(), h.end(), v.begin());
        std::copy(iv.begin(), iv.end(), v.begin() + 8);
        v[12] ^= counted;
        if (last)
          v[14] = ~v[14];
        auto mix = [&](int a, int b, int c, int d, std::uint64_t x, std::uint64_t y) {
          v[a] = v[a] + v[b] + x;
          v[d] = std::rotr(v[d] ^ v[a], 32);
          v[c] = v[c] + v[d];
          v[b] = std::rotr(v[b] ^ v[c], 24);
          v[a] = v[a] + v[b] + y;
          v[d] = std::rotr(v[d] ^ v[a], 16);
          v[c] = v[c] + v[d];
          v[b] = std::rotr(v[b] ^ v[c], 63);
        };
        for (const auto& s : sigma) {
          mix(0, 4, 8, 12, m[s[0]], m[s[1]]);
          mix(1, 5, 9, 13, m[s[2]], m[s[3]]);
          mix(2, 6, 10, 14, m[s[4]], m[s[5]]);
          mix(3, 7, 11, 15, m[s[6]], m[s[7]]);
          mix(0, 5, 10, 15, m[s[8]], m[s[9]]);
          mix(1, 6, 11, 12, m[s[10]], m[s[11]]);
          mix(2, 7, 8, 13, m[s[12]], m[s[13]]);
          mix(3, 4, 9, 14, m[s[14]], m[s[15]]);
        }
        for (std::size_t i = 0; i < 8; ++i)
          h[i] ^= v[i] ^ v[i + 8];
      }

      std::size_t digest;
      std::array<std::uint64_t, 8> h{};
      std::array<unsigned char, 128> block{};
      std::size_t filled = 0;
      // The input is never more than a path, so the high word of the counter
      // stays nought.
      std::uint64_t counted = 0;
    };


    bool is_file(const fs::path& path)
    {
      std::error_code ec;
      return fs::is_regular_file(path, ec);
    }

  } // anonymous namespace


  fs::path pure_path(const fs::path& path)
  {
    fs::path out;
    bool rooted = path.has_root_directory();
    if (rooted)
      out = path.root_path();
    for (const auto& part : path.relative_path())
      if (! part.empty() && part != ".")
        out /= part;
    return out.empty() ? fs::path(".") : out;
  }


  fs::path parent_of(const fs::path& path)
  {
    auto pure = pure_path(path);
    if (pure == "." || pure == pure.root_path())
      return pure;
    return pure_path(pure.parent_path());
  }


  std::string LoadedModule::add_candidate(std::string_view prefix, std::string_view base)
  {
    std::string candidate = prefix.empty() ? std::string(base) : std::format("{}.{}", prefix, base);
    candidates.insert(candidate);
    return candidate;
  }


  std::vector<fs::path> SearchPath::places(std::string_view name, const fs::path& importer) const
  {
    std::string wanted(name);
    if (! wanted.ends_with(module_suffix))
      wanted += module_suffix;
    fs::path candidate = pure_path(wanted);
    if (candidate.is_absolute())
      // A name that is a path names one file and no search is done.
      return { candidate };
    auto from = parent_of(importer);
    std::vector<fs::path> found{ pure_path(from / candidate) };
    for (const auto& directory : given)
      if (directory.is_absolute())
        found.push_back(pure_path(directory / candidate));
      else {
        // Relative to the importing file first and to where the compiler was
        // run second.
        found.push_back(pure_path(from / directory / candidate));
        found.push_back(pure_path(working / directory / candidate));
      }
    if (name.find('/') == std::string_view::npos)
      for (const auto& directory : system)
        found.push_back(pure_path(directory / candidate));
    return found;
  }


  std::vector<fs::path> SearchPath::system_modules()
  {
    // Beside the compiler's own sources, so that a checkout and an install
    // behave the same: this file is src/lib/sema/modules.cc.
    std::error_code ec;
    auto here = fs::weakly_canonical(fs::absolute(__FILE__), ec);
    return { pure_path(here.parent_path().parent_path().parent_path().parent_path() / "modules") };
  }


  fs::path ModuleRegistry::resolve(std::string_view name, const fs::path& importer) const
  {
    auto looked = search.places(name, importer);
    for (const auto& place : looked)
      if (is_file(place)) {
        // The same file reached two ways is one module, so what is remembered
        // is what the file system says it is.
        std::error_code ec;
        auto resolved = fs::weakly_canonical(fs::absolute(place), ec);
        return pure_path(ec ? fs::absolute(place) : resolved);
      }
    throw ModuleNotFound(std::string(name), std::move(looked));
  }


  LoadedModule* ModuleRegistry::find(const fs::path& path) const
  {
    for (const auto& one : by_path)
      if (one->path == path)
        return one.get();
    return nullptr;
  }


  bool ModuleRegistry::being_read(const fs::path& path) const
  {
    return std::ranges::find(reading, path) != reading.end();
  }


  LoadedModule* ModuleRegistry::known(const fs::path& path) const
  {
    auto found = find(path);
    return found != nullptr && being_read(path) ? nullptr : found;
  }


  std::optional<std::vector<fs::path>> ModuleRegistry::cycle_through(const fs::path& path) const
  {
    auto at = std::ranges::find(reading, path);
    if (at == reading.end())
      return std::nullopt;
    std::vector<fs::path> ring(at, reading.end());
    ring.push_back(path);
    return ring;
  }


  LoadedModule& ModuleRegistry::begin(const fs::path& path)
  {
    if (auto ring = cycle_through(path)) [[unlikely]]
      throw ImportCycle(std::move(*ring));
    reading.push_back(path);
    // A path read before is read again as a new module, as the Python's
    // `by_path[path] = found` replaces the old entry in its place.
    auto made = std::make_unique<LoadedModule>();
    made->path = path;
    auto& result = *made;
    auto old = std::ranges::find_if(by_path, [&](const auto& one) { return one->path == path; });
    if (old != by_path.end())
      *old = std::move(made);
    else
      by_path.push_back(std::move(made));
    return result;
  }


  void ModuleRegistry::finish(const fs::path& path)
  {
    if (! reading.empty() && reading.back() == path)
      reading.pop_back();
  }


  void ModuleRegistry::settle_names()
  {
    for (const auto& module : by_path)
      if (module->candidates.empty())
        module->name = base_name(module->path);
      else
        module->name = *std::ranges::min_element(module->candidates, [](const auto& a, const auto& b) {
          return a.size() != b.size() ? a.size() < b.size() : a < b;
        });
    disambiguate();
    for (const auto& module : by_path)
      for (auto owned : module->owned)
        if (auto func = dynamic_cast<ir::Function*>(owned))
          func->module = module->name;
        else if (auto var = dynamic_cast<ir::GlobalVar*>(owned))
          var->module = module->name;
  }


  void ModuleRegistry::disambiguate()
  {
    OrderedMap<std::string, std::vector<LoadedModule*>> by_name;
    for (const auto& module : by_path)
      by_name[module->name].push_back(module.get());
    for (auto& [name, sharing] : by_name) {
      if (sharing.size() < 2)
        continue;
      for (auto module : sharing)
        module->name = std::format("{}-{}", name, path_hash(module->path));
    }
  }


  std::string base_name(const fs::path& path)
  {
    auto name = pure_path(path).filename().string();
    if (name == ".")
      name.clear();
    return name.ends_with(module_suffix) ? name.substr(0, name.size() - module_suffix.size()) : name;
  }


  std::string path_hash(const fs::path& path)
  {
    auto text = pure_path(path).string();
    Blake2b hash(hash_length);
    hash.update(std::span(reinterpret_cast<const unsigned char*>(text.data()), text.size()));
    std::string hex;
    for (auto byte : hash.finish())
      hex += std::format("{:02x}", byte);
    return hex.substr(0, hash_length);
  }


  std::vector<fs::path> parse_search_path(std::string_view given)
  {
    std::vector<fs::path> found;
    for (auto piece : given | std::views::split(':'))
      if (! piece.empty())
        found.push_back(pure_path(std::string_view(piece)));
    return found;
  }

} // namespace pl4g::sema
