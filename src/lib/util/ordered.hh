// Containers that keep the order things were put in, as Python's dict does.
//
// The checker is a port of code that iterates its dictionaries in insertion
// order and lets that order show -- in the order functions are made, the order
// diagnostics are said -- so a map ported from one has to keep it too.
#pragma once

#include <cstddef>
#include <functional>
#include <list>
#include <optional>
#include <tuple>
#include <type_traits>
#include <unordered_map>
#include <utility>
#include <vector>


namespace pl4g {

  // A hash for what Python would hash: pairs, tuples and vectors of what has
  // a `std::hash`, combined as boost does.
  template<typename T>
  struct Hash {
    std::size_t operator()(const T& value) const noexcept { return std::hash<T>{}(value); }
  };

  inline std::size_t hash_combine(std::size_t seed, std::size_t one) noexcept
  {
    return seed ^ (one + 0x9e3779b97f4a7c15ull + (seed << 6) + (seed >> 2));
  }

  template<typename A, typename B>
  struct Hash<std::pair<A, B>> {
    std::size_t operator()(const std::pair<A, B>& value) const noexcept
    {
      return hash_combine(Hash<A>{}(value.first), Hash<B>{}(value.second));
    }
  };

  template<typename... Ts>
  struct Hash<std::tuple<Ts...>> {
    std::size_t operator()(const std::tuple<Ts...>& value) const noexcept
    {
      return std::apply([](const auto&... one) {
        std::size_t seed = 0;
        ((seed = hash_combine(seed, Hash<std::remove_cvref_t<decltype(one)>>{}(one))), ...);
        return seed;
      }, value);
    }
  };

  template<typename T>
  struct Hash<std::vector<T>> {
    std::size_t operator()(const std::vector<T>& value) const noexcept
    {
      std::size_t seed = value.size();
      for (const auto& one : value)
        seed = hash_combine(seed, Hash<T>{}(one));
      return seed;
    }
  };

  template<typename T>
  struct Hash<std::optional<T>> {
    std::size_t operator()(const std::optional<T>& value) const noexcept
    {
      return value ? hash_combine(1, Hash<T>{}(*value)) : 0;
    }
  };


  // A map that iterates in the order its keys were first put in, and keeps
  // that order when a value is replaced; erasing a key and putting it in
  // again puts it last.
  template<typename K, typename V, typename H = Hash<K>, typename Eq = std::equal_to<K>>
  class OrderedMap {
  public:
    using value_type = std::pair<const K, V>;
    using iterator = typename std::list<value_type>::iterator;
    using const_iterator = typename std::list<value_type>::const_iterator;

    OrderedMap() = default;
    OrderedMap(std::initializer_list<std::pair<K, V>> items)
    {
      for (auto& [k, v] : items)
        (*this)[k] = v;
    }
    OrderedMap(const OrderedMap& other) { *this = other; }
    OrderedMap(OrderedMap&&) noexcept = default;
    OrderedMap& operator=(const OrderedMap& other)
    {
      if (this != &other) {
        clear();
        for (const auto& [k, v] : other)
          emplace(k, v);
      }
      return *this;
    }
    OrderedMap& operator=(OrderedMap&&) noexcept = default;

    iterator begin() noexcept { return items.begin(); }
    iterator end() noexcept { return items.end(); }
    const_iterator begin() const noexcept { return items.begin(); }
    const_iterator end() const noexcept { return items.end(); }

    std::size_t size() const noexcept { return items.size(); }
    bool empty() const noexcept { return items.empty(); }
    void clear() noexcept
    {
      index.clear();
      items.clear();
    }

    bool contains(const K& key) const { return index.contains(key); }

    iterator find(const K& key)
    {
      auto it = index.find(key);
      return it == index.end() ? items.end() : it->second;
    }
    const_iterator find(const K& key) const
    {
      auto it = index.find(key);
      return it == index.end() ? items.end() : const_iterator(it->second);
    }

    // The value under `key`, or nothing: Python's `d.get(key)`.
    V* get(const K& key)
    {
      auto it = index.find(key);
      return it == index.end() ? nullptr : &it->second->second;
    }
    const V* get(const K& key) const
    {
      auto it = index.find(key);
      return it == index.end() ? nullptr : &it->second->second;
    }
    // The value under `key`, or `otherwise`: Python's `d.get(key, otherwise)`.
    V get_or(const K& key, V otherwise) const
    {
      const V* found = get(key);
      return found == nullptr ? otherwise : *found;
    }

    V& operator[](const K& key)
    {
      auto it = index.find(key);
      if (it != index.end())
        return it->second->second;
      return emplace(key, V{}).first->second;
    }

    std::pair<iterator, bool> emplace(const K& key, V value)
    {
      auto it = index.find(key);
      if (it != index.end())
        return { it->second, false };
      items.emplace_back(key, std::move(value));
      auto last = std::prev(items.end());
      index.emplace(key, last);
      return { last, true };
    }

    // Python's `setdefault`.
    V& setdefault(const K& key, V value) { return emplace(key, std::move(value)).first->second; }

    bool erase(const K& key)
    {
      auto it = index.find(key);
      if (it == index.end())
        return false;
      items.erase(it->second);
      index.erase(it);
      return true;
    }

    // Python's `pop(key, None)`.
    std::optional<V> pop(const K& key)
    {
      auto it = index.find(key);
      if (it == index.end())
        return std::nullopt;
      V value = std::move(it->second->second);
      items.erase(it->second);
      index.erase(it);
      return value;
    }

    std::vector<K> keys() const
    {
      std::vector<K> all;
      all.reserve(items.size());
      for (const auto& one : items)
        all.push_back(one.first);
      return all;
    }

  private:
    std::list<value_type> items;
    std::unordered_map<K, iterator, H, Eq> index;
  };


  // A set that iterates in the order its elements were first put in.  Python's
  // sets do not, but where their order cannot show this is as good, and where
  // it can the port says why.
  template<typename K, typename H = Hash<K>, typename Eq = std::equal_to<K>>
  class OrderedSet {
  public:
    OrderedSet() = default;
    OrderedSet(std::initializer_list<K> items)
    {
      for (const auto& k : items)
        insert(k);
    }

    auto begin() const noexcept { return items.begin(); }
    auto end() const noexcept { return items.end(); }
    std::size_t size() const noexcept { return items.size(); }
    bool empty() const noexcept { return items.empty(); }
    bool contains(const K& key) const { return index.contains(key); }

    bool insert(const K& key)
    {
      if (index.contains(key))
        return false;
      items.push_back(key);
      index.emplace(key, std::prev(items.end()));
      return true;
    }

    bool erase(const K& key)
    {
      auto it = index.find(key);
      if (it == index.end())
        return false;
      items.erase(it->second);
      index.erase(it);
      return true;
    }

    void clear() noexcept
    {
      index.clear();
      items.clear();
    }

  private:
    std::list<K> items;
    std::unordered_map<K, typename std::list<K>::iterator, H, Eq> index;
  };

} // namespace pl4g
