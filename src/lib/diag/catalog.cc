#include "diag/catalog.hh"

#include <algorithm>
#include <contracts>


namespace pl4g {

  const DiagInfo& catalog_entry(DiagId id) noexcept
  {
    auto it = std::ranges::lower_bound(catalog_entries, id, {}, &DiagInfo::number);
    contract_assert(it != std::end(catalog_entries) && it->number == id);
    return *it;
  }


  const DiagInfo* catalog_by_name(std::string_view name) noexcept
  {
    auto it = std::ranges::find(catalog_entries, name, &DiagInfo::name);
    return it == std::end(catalog_entries) ? nullptr : it;
  }


  const DiagInfo* catalog_by_option(std::string_view option) noexcept
  {
    if (option.empty())
      return nullptr;
    auto it = std::ranges::find(catalog_entries, option, &DiagInfo::option);
    return it == std::end(catalog_entries) ? nullptr : it;
  }

} // namespace pl4g
