// What a Python variable annotated `object` holds in the checker: what a name
// at the top level stands for, and whatever else check.py hands around without
// saying what it is.
#pragma once

#include <string>
#include <type_traits>
#include <variant>

#include "front/ast.hh"
#include "ir/ir.hh"


namespace pl4g::sema {

  struct Bundle;
  struct Generator;
  struct Generic;
  struct LoadedModule;
  struct Local;
  struct NamedType;

  // What `_MIXED` is: two parts of something written say different types.
  struct Mixed {
    bool operator==(const Mixed&) const = default;
  };

  // What `_REFUSED` is where it is handed around as an `object`: a rewriting
  // that refused what it was given, and said why.
  struct Refused {
    bool operator==(const Refused&) const = default;
  };


  // What a Python variable annotated `object` holds: whichever of the
  // checker's things it is.  A name at the top level stands for a variable, a
  // function, a module, a generator, a generic function or a type; other
  // places hand around a type, a value, a node, a number or text the same way.
  // Empty is Python's `None`.  Compared by identity, as `is` compares.
  struct Held {
    std::variant<std::monostate, const ir::Type*, ir::Value*, LoadedModule*, Generator*, Generic*, NamedType*,
                 Local*, Bundle*, const ast::Node*, Mixed, Refused, std::string, __int128, bool>
      v;

    Held() = default;
    Held(std::nullptr_t) {}
    Held(const ir::Type* ty) { if (ty != nullptr) v = ty; }
    Held(ir::Value* value) { if (value != nullptr) v = value; }
    Held(LoadedModule* m) { if (m != nullptr) v = m; }
    Held(Generator* g) { if (g != nullptr) v = g; }
    Held(Generic* g) { if (g != nullptr) v = g; }
    Held(NamedType* t) { if (t != nullptr) v = t; }
    Held(Local* l) { if (l != nullptr) v = l; }
    Held(Bundle* b) { if (b != nullptr) v = b; }
    Held(const ast::Node* n) { if (n != nullptr) v = n; }
    Held(Mixed m) : v(m) {}
    Held(Refused r) : v(r) {}
    Held(std::string s) : v(std::move(s)) {}
    Held(__int128 n) : v(n) {}
    explicit Held(bool b) : v(b) {}

    bool empty() const noexcept { return std::holds_alternative<std::monostate>(v); }
    explicit operator bool() const noexcept { return ! empty(); }
    bool operator==(const Held&) const = default;

    // What it holds as a T, or nothing: Python's `isinstance`.  A function and
    // a global are values, and asked for as what they are.
    template<typename T>
    T* get() const noexcept
    {
      if constexpr (std::is_same_v<T, ir::Function> || std::is_same_v<T, ir::GlobalVar>) {
        auto value = std::get_if<ir::Value*>(&v);
        if (value == nullptr)
          return nullptr;
        constexpr auto kind = std::is_same_v<T, ir::Function> ? ir::ValueKind::function : ir::ValueKind::global;
        return (*value)->vkind == kind ? static_cast<T*>(*value) : nullptr;
      } else if constexpr (std::is_same_v<T, ir::Value>) {
        auto value = std::get_if<ir::Value*>(&v);
        return value == nullptr ? nullptr : *value;
      } else if constexpr (std::is_base_of_v<ir::Type, T>) {
        auto ty = std::get_if<const ir::Type*>(&v);
        if (ty == nullptr)
          return nullptr;
        if constexpr (std::is_same_v<T, ir::Type>)
          return const_cast<T*>(*ty);
        else
          return const_cast<T*>(ir::as<T>(*ty));
      } else if constexpr (std::is_base_of_v<ast::Node, T>) {
        auto node = std::get_if<const ast::Node*>(&v);
        if (node == nullptr)
          return nullptr;
        if constexpr (std::is_same_v<T, ast::Node>)
          return const_cast<T*>(*node);
        else
          return const_cast<T*>(ast::as<T>(*node));
      } else {
        auto p = std::get_if<T*>(&v);
        return p == nullptr ? nullptr : *p;
      }
    }

    // The type it holds, where it holds one.
    const ir::Type* type() const noexcept
    {
      auto ty = std::get_if<const ir::Type*>(&v);
      return ty == nullptr ? nullptr : *ty;
    }
    const std::string* text() const noexcept { return std::get_if<std::string>(&v); }
    const __int128* number() const noexcept { return std::get_if<__int128>(&v); }
    bool is_mixed() const noexcept { return std::holds_alternative<Mixed>(v); }
    bool is_refused() const noexcept { return std::holds_alternative<Refused>(v); }
  };


} // namespace pl4g::sema
