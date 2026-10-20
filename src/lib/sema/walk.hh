// Walking what a node holds, as pypl4g's walks over `dataclasses.fields` do.
//
// Those walks see more than the nodes below a node: a name written beside
// where something is made (`in a`, a `(str, Span)` pair there) is a use of
// that name, and what it is keyed by is the pair itself.  So the walk here
// hands on both, the pair by its address in the node, which is as lasting as
// the node is.  The fields pypl4g's walks leave out -- `span` and `attrs` --
// are left out here too.
#pragma once

#include <meta>
#include <optional>
#include <string_view>
#include <type_traits>

#include "front/ast.hh"


namespace pl4g::sema {

  namespace walk_detail {

    template<typename N, typename P>
    void field_value(const auto& v, N& on_node, P& on_name)
    {
      using T = std::remove_cvref_t<decltype(v)>;
      if constexpr (std::is_pointer_v<T>) {
        if constexpr (std::is_base_of_v<ast::Node, std::remove_cv_t<std::remove_pointer_t<T>>>)
          if (v != nullptr)
            on_node(static_cast<const ast::Node*>(v));
      } else if constexpr (std::is_same_v<T, ast::NameAt>)
        on_name(&v);
      else if constexpr (std::is_same_v<T, ast::ExprPair>) {
        field_value(v.key, on_node, on_name);
        field_value(v.value, on_node, on_name);
      } else if constexpr (std::is_same_v<T, ast::UnitPair>) {
        field_value(v.what, on_node, on_name);
        field_value(v.where, on_node, on_name);
      } else if constexpr (requires { v.has_value(); *v; }) {
        if (v.has_value())
          field_value(*v, on_node, on_name);
      } else if constexpr (requires { v.begin(); v.end(); } && ! std::is_same_v<T, std::string_view>)
        for (const auto& one : v)
          field_value(one, on_node, on_name);
    }

    template<typename T, typename N, typename P>
    void fields_of(const T& node, N& on_node, P& on_name)
    {
      constexpr auto ctx = std::meta::access_context::unchecked();
      template for (constexpr auto b : std::define_static_array(std::meta::bases_of(^^T, ctx)))
        fields_of(static_cast<const [:std::meta::type_of(b):]&>(node), on_node, on_name);
      template for (constexpr auto m : std::define_static_array(std::meta::nonstatic_data_members_of(^^T, ctx)))
        if constexpr (std::meta::annotations_of_with_type(m, ^^ast::Hidden).size() == 0
                      && std::meta::identifier_of(m) != std::string_view("attrs"))
          field_value(node.[:m:], on_node, on_name);
    }

  } // namespace walk_detail


  // Hand `on_node` every node `node` holds and `on_name` every name written
  // with where it was, in the order of the fields holding them, leaving out
  // `attrs`.  A block's fields are its statements.
  template<typename N, typename P>
  void walk_fields(const ast::Node* node, N&& on_node, P&& on_name)
  {
    if (node == nullptr)
      return;
    if (auto block = ast::as<ast::Block>(node)) {
      for (auto stmt : block->stmts())
        on_node(static_cast<const ast::Node*>(stmt));
      return;
    }
    ast::visit(*node, [&](const auto& n) { walk_detail::fields_of(n, on_node, on_name); });
  }

  // Whether `node` is a statement: pypl4g's `isinstance(node, ast.Stmt)`.
  inline bool is_stmt(const ast::Node* node) noexcept
  {
    if (node == nullptr)
      return false;
    using K = ast::NodeKind;
    switch (node->tag) {
    case K::ReturnStmt: case K::Yield: case K::ExprStmt: case K::EmptyStmt: case K::VarDef: case K::AssignStmt:
    case K::JoinAssign: case K::DerefAssign: case K::ElementAssign: case K::HoleAssign: case K::MemberAssign:
    case K::EntryAssign: case K::UnitDef: case K::Break: case K::Defer: case K::Continue:
      return true;
    default:
      return false;
    }
  }

} // namespace pl4g::sema
