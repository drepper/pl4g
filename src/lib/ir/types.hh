// IR types.
//
// No type carries a size, an alignment or a field offset: the language lets
// the compiler reorder a record's fields, so layout is computed late and held
// beside a type (ir/layout.hh), never inside it.
//
// A type built out of others is interned, once for the whole process, so two
// of them are the same type exactly when they are the same object -- what
// pypl4g gets from frozen dataclasses comparing by their fields.  A record, a
// sum, a generator's frame and a type parameter are nominal and are not
// interned: each one made is a type of its own.
#pragma once

#include <compare>
#include <cstdint>
#include <optional>
#include <span>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "util/reflect.hh"


namespace pl4g::ir {

  // A fraction, exact, in lowest terms with a positive denominator.
  struct Rational {
    __int128 num = 1;
    __int128 den = 1;

    static Rational of(__int128 n, __int128 d);
    Rational operator*(const Rational& o) const { return of(num * o.num, den * o.den); }
    Rational operator/(const Rational& o) const { return of(num * o.den, den * o.num); }
    Rational power(std::int64_t exponent) const;
    bool operator==(const Rational&) const = default;
    auto operator<=>(const Rational& o) const { return std::pair(num, den) <=> std::pair(o.num, o.den); }
  };


  // What a number counts: base units with exponents, sorted by name and none
  // of them nought, and how many of the base units one of these is.  Interned,
  // so the empty one -- no unit at all -- is one object.
  struct Unit {
    std::vector<std::pair<std::string, std::int64_t>> powers;
    Rational scale;

    // How the unit is written in a type: `meter÷second²`.
    std::string render() const;
    // The unit as one word, for a symbol name.
    std::string mangled() const;
    // The unit of a product, a quotient and a power.
    const Unit* times(const Unit& other) const;
    const Unit* over(const Unit& other) const;
    const Unit* raised(std::int64_t exponent) const;
    // Whether this is no unit at all.
    bool is_none() const noexcept { return powers.empty() && scale == Rational{}; }

    // The interned unit with these parts.
    static const Unit* make(std::vector<std::pair<std::string, std::int64_t>> powers, Rational scale);
    // No unit at all.
    static const Unit* none();

    auto operator<=>(const Unit&) const = default;
  };


  enum class TypeKind : std::uint8_t {
    void_, bool_, char_, syntax, int_, float_, ptr, frame, running, iter, func, opaque, mem, error,
    product, result, tuple, array, vec, str, list, cursor, set, dict, enum_, sum,
  };


  // Base of every type.
  struct Type {
    TypeKind kind;

    explicit Type(TypeKind kind) noexcept : kind(kind) {}
    Type(const Type&) = delete;
    Type& operator=(const Type&) = delete;
    virtual ~Type() = default;

    // The name in the textual form of the IR.
    virtual std::string render() const = 0;
    // The name as a program writes it, which is what a message says.
    virtual std::string written() const { return render(); }
    // The name inside a symbol: no spaces, no glyphs.
    virtual std::string mangled() const { return render(); }
  };


  // `T::kind_of` is the kind each struct below is.
  template<typename T>
  bool is(const Type* ty) noexcept
  {
    return ty != nullptr && ty->kind == T::kind_of;
  }

  template<typename T>
  const T* as(const Type* ty) noexcept
  {
    return is<T>(ty) ? static_cast<const T*>(ty) : nullptr;
  }


  struct VoidType final : Type {
    static constexpr TypeKind kind_of = TypeKind::void_;
    VoidType() : Type(kind_of) {}
    std::string render() const override { return "void"; }
  };

  // Truth values; not a one-bit integer.
  struct BoolType final : Type {
    static constexpr TypeKind kind_of = TypeKind::bool_;
    BoolType() : Type(kind_of) {}
    std::string render() const override { return "bool"; }
  };

  struct IntType;

  // One Unicode code point, held as a `u32` and not an integer type.
  struct CharType final : Type {
    static constexpr TypeKind kind_of = TypeKind::char_;
    CharType() : Type(kind_of) {}
    std::string render() const override { return "char"; }
    const IntType* holder() const noexcept;
  };

  // A piece of the program, a handle the size of a machine word.
  struct SyntaxType final : Type {
    static constexpr TypeKind kind_of = TypeKind::syntax;
    SyntaxType() : Type(kind_of) {}
    std::string render() const override { return "syntax"; }
    const IntType* holder() const noexcept;
  };

  // An integer of any width up to sixty-four, held in the narrowest machine
  // width that contains it.
  struct IntType final : Type {
    static constexpr TypeKind kind_of = TypeKind::int_;
    unsigned bits;
    bool is_signed;
    const Unit* unit;

    IntType(unsigned bits, bool is_signed, const Unit* unit) : Type(kind_of), bits(bits), is_signed(is_signed), unit(unit) {}
    std::string render() const override;
    std::string mangled() const override;

    // The same width and signedness with no unit, which is what the bits are.
    const IntType* bare() const;
    // How many bits the register and the place holding one have.
    unsigned held() const noexcept { return bits <= 8 ? 8 : bits <= 16 ? 16 : bits <= 32 ? 32 : 64; }
    bool whole() const noexcept { return bits == held(); }
    __int128 low() const noexcept { return is_signed ? -(__int128(1) << (bits - 1)) : 0; }
    __int128 high() const noexcept
    {
      return is_signed ? (__int128(1) << (bits - 1)) - 1 : (__int128(1) << bits) - 1;
    }
    bool holds(__int128 value) const noexcept { return low() <= value && value <= high(); }
  };

  struct FloatType final : Type {
    static constexpr TypeKind kind_of = TypeKind::float_;
    unsigned bits;
    const Unit* unit;

    FloatType(unsigned bits, const Unit* unit) : Type(kind_of), bits(bits), unit(unit) {}
    std::string render() const override;
    std::string mangled() const override;
    const FloatType* bare() const;
  };

  // A pointer.  Whether the place may be written, and whether it lives as long
  // as the program, are the pointer's and not the pointee's.
  struct PtrType final : Type {
    static constexpr TypeKind kind_of = TypeKind::ptr;
    const Type* pointee;
    bool is_mutable;
    bool lasting;

    PtrType(const Type* pointee, bool is_mutable, bool lasting)
      : Type(kind_of), pointee(pointee), is_mutable(is_mutable), lasting(lasting) {}
    std::string render() const override;
    std::string written() const override;
    std::string mangled() const override;
  };

  // Room for a generator stopped part way; measured by the pass that splits its
  // body, and told apart by identity.
  struct CoroutineFrame final : Type {
    static constexpr TypeKind kind_of = TypeKind::frame;
    std::string name;

    explicit CoroutineFrame(std::string name) : Type(kind_of), name(std::move(name)) {}
    std::string render() const override { return "frame<" + name + ">"; }
    std::string written() const override { return "the frame of '" + name + "'"; }
  };

  // A walk under way, driven by hand: four words.
  struct RunningType final : Type {
    static constexpr TypeKind kind_of = TypeKind::running;
    const Type* element;
    const Type* given;
    bool lasting;

    RunningType(const Type* element, const Type* given, bool lasting)
      : Type(kind_of), element(element), given(given), lasting(lasting) {}
    std::string render() const override;
    std::string written() const override;
    std::string mangled() const override;
  };

  // `⇉T`: something a loop can walk; nothing for `⇉` written alone.
  struct IterType final : Type {
    static constexpr TypeKind kind_of = TypeKind::iter;
    const Type* element;

    explicit IterType(const Type* element) : Type(kind_of), element(element) {}
    std::string render() const override;
    std::string written() const override;
    std::string mangled() const override;
  };

  // What a reference in a function's answer lives as long as.
  struct Borrowed {
    std::vector<int> params;
    std::vector<std::optional<std::vector<int>>> leaves;
    std::vector<std::pair<std::string, std::string>> outer;

    auto operator<=>(const Borrowed&) const = default;
  };

  struct FuncType final : Type {
    static constexpr TypeKind kind_of = TypeKind::func;
    std::vector<const Type*> params;
    const Type* ret;
    bool listable = false;
    bool hands_out = false;
    bool lasting = false;
    bool impure = false;
    bool can_ignore = false;
    std::vector<std::pair<int, Borrowed>> borrows;

    FuncType() : Type(kind_of) {}
    std::string render() const override;
    std::string written() const override;
    std::string mangled() const override;

  private:
    std::string said(bool glyphs) const;
  };

  // A type parameter while its generic function is checked where it is
  // written, told apart by identity.
  struct OpaqueType final : Type {
    static constexpr TypeKind kind_of = TypeKind::opaque;
    std::string name;

    explicit OpaqueType(std::string name) : Type(kind_of), name(std::move(name)) {}
    std::string render() const override { return name; }
  };

  // The token that threads memory effects through the graph.
  struct MemType final : Type {
    static constexpr TypeKind kind_of = TypeKind::mem;
    MemType() : Type(kind_of) {}
    std::string render() const override { return "mem"; }
  };

  // Stands in for a type that could not be worked out.
  struct ErrorType final : Type {
    static constexpr TypeKind kind_of = TypeKind::error;
    ErrorType() : Type(kind_of) {}
    std::string render() const override { return "<error>"; }
  };

  // A record, nominal; its fields in the order declared.
  struct ProductType final : Type {
    static constexpr TypeKind kind_of = TypeKind::product;
    std::vector<std::pair<std::string, const Type*>> fields;
    std::string name;
    std::string origin;
    bool abi = false;
    bool unique = false;
    bool device = false;
    std::vector<std::string> mutable_fields;
    std::string closed_by;
    // The definition a record of a generic type was made from, and its types.
    const void* family = nullptr;
    std::vector<const Type*> args;

    ProductType() : Type(kind_of) {}
    std::string render() const override;
    std::string mangled() const override;
  };

  // A value, or the fact that it could not be produced.
  struct ResultType final : Type {
    static constexpr TypeKind kind_of = TypeKind::result;
    const Type* ok;
    const Type* err;

    ResultType(const Type* ok, const Type* err) : Type(kind_of), ok(ok), err(err) {}
    std::string render() const override;
    std::string written() const override;
    std::string mangled() const override;
  };

  struct TupleType final : Type {
    static constexpr TypeKind kind_of = TypeKind::tuple;
    std::vector<const Type*> members;

    explicit TupleType(std::vector<const Type*> members) : Type(kind_of), members(std::move(members)) {}
    std::string render() const override;
    std::string written() const override;
    std::string mangled() const override;
  };

  // Several values of one type, one after another, row-major.  A dimension is
  // a number where the type says how many and nothing where it does not.
  struct ArrayType final : Type {
    static constexpr TypeKind kind_of = TypeKind::array;
    const Type* element;
    std::vector<std::optional<std::uint64_t>> shape;

    ArrayType(const Type* element, std::vector<std::optional<std::uint64_t>> shape)
      : Type(kind_of), element(element), shape(std::move(shape)) {}
    std::size_t rank() const noexcept { return shape.size(); }
    bool fixed() const noexcept;
    std::optional<std::uint64_t> count() const noexcept;
    std::string render() const override;
    std::string written() const override;
    std::string mangled() const override;

  private:
    std::string dimensions() const;
  };

  // Several values held side by side as one: what an operator over an array
  // works on.
  struct VecType final : Type {
    static constexpr TypeKind kind_of = TypeKind::vec;
    const Type* element;
    unsigned lanes;

    VecType(const Type* element, unsigned lanes) : Type(kind_of), element(element), lanes(lanes) {}
    std::string render() const override;
    std::string written() const override;
    std::string mangled() const override;
  };

  // Text, encoded as UTF-8: where the bytes are, how many, and their arena.
  struct StrType final : Type {
    static constexpr TypeKind kind_of = TypeKind::str;
    StrType() : Type(kind_of) {}
    std::string render() const override { return "str"; }
  };

  struct ListType final : Type {
    static constexpr TypeKind kind_of = TypeKind::list;
    const Type* element;

    explicit ListType(const Type* element) : Type(kind_of), element(element) {}
    std::string render() const override { return "[" + element->render() + "]"; }
    std::string written() const override { return "[" + element->written() + "]"; }
    std::string mangled() const override { return "list<" + element->mangled() + ">"; }
  };

  // Where a walk over a list has got to.
  struct CursorType final : Type {
    static constexpr TypeKind kind_of = TypeKind::cursor;
    const Type* element;

    explicit CursorType(const Type* element) : Type(kind_of), element(element) {}
    std::string render() const override { return "cursor<" + element->render() + ">"; }
    std::string written() const override { return "cursor over [" + element->written() + "]"; }
    std::string mangled() const override { return "cursor<" + element->mangled() + ">"; }
  };

  struct SetType final : Type {
    static constexpr TypeKind kind_of = TypeKind::set;
    const Type* element;
    bool is_mutable;

    SetType(const Type* element, bool is_mutable) : Type(kind_of), element(element), is_mutable(is_mutable) {}
    std::string render() const override;
    std::string written() const override;
    std::string mangled() const override;
  };

  struct DictType final : Type {
    static constexpr TypeKind kind_of = TypeKind::dict;
    const Type* key;
    const Type* value;
    bool is_mutable;

    DictType(const Type* key, const Type* value, bool is_mutable)
      : Type(kind_of), key(key), value(value), is_mutable(is_mutable) {}
    std::string render() const override;
    std::string written() const override;
    std::string mangled() const override;
  };

  // A fixed set of named values, nominal by name and origin.
  struct EnumType final : Type {
    static constexpr TypeKind kind_of = TypeKind::enum_;
    std::vector<std::string> members;
    std::vector<std::int64_t> values;
    const IntType* holder;
    bool flag = false;
    std::string name;
    std::string origin;

    EnumType() : Type(kind_of), holder(nullptr) {}
    std::string render() const override;
    std::string mangled() const override;
    std::optional<std::size_t> index_of(std::string_view member) const;
  };

  // A choice between named variants, nominal.
  struct SumType final : Type {
    static constexpr TypeKind kind_of = TypeKind::sum;
    std::vector<std::pair<std::string, const Type*>> variants;
    std::string name;
    std::string origin;
    const void* family = nullptr;
    std::vector<const Type*> args;

    SumType() : Type(kind_of) {}
    std::string render() const override;
    std::string mangled() const override;
  };


  // -- making types -----------------------------------------------------------

  const VoidType* void_type();
  const BoolType* bool_type();
  const CharType* char_type();
  const SyntaxType* syntax_type();
  const MemType* mem_type();
  const ErrorType* error_type();
  const StrType* str_type();
  const IntType* int_type(unsigned bits, bool is_signed, const Unit* unit = nullptr);
  const FloatType* float_type(unsigned bits, const Unit* unit = nullptr);
  const PtrType* ptr_type(const Type* pointee, bool is_mutable = false, bool lasting = false);
  const RunningType* running_type(const Type* element, const Type* given, bool lasting = false);
  const IterType* iter_type(const Type* element);
  const ResultType* result_type(const Type* ok, const Type* err = nullptr);
  const TupleType* tuple_type(std::vector<const Type*> members);
  const ArrayType* array_type(const Type* element, std::vector<std::optional<std::uint64_t>> shape = { std::nullopt });
  const VecType* vec_type(const Type* element, unsigned lanes);
  const ListType* list_type(const Type* element);
  const CursorType* cursor_type(const Type* element);
  const SetType* set_type(const Type* element, bool is_mutable = false);
  const DictType* dict_type(const Type* key, const Type* value, bool is_mutable = false);
  // The function type with this signature; `shape` carries the flags and the
  // borrows, its params and ret being ignored.
  const FuncType* func_type(std::vector<const Type*> params, const Type* ret, const FuncType& shape);
  const FuncType* func_type(std::vector<const Type*> params, const Type* ret);
  const EnumType* enum_type(std::vector<std::string> members, std::vector<std::int64_t> values,
                            const IntType* holder, bool flag, std::string name, std::string origin);

  // Nominal types, each a new one, kept for the life of the process.  What
  // comes back may be filled in until anything else reads it.
  ProductType* new_product();
  SumType* new_sum();
  CoroutineFrame* new_frame(std::string name);
  OpaqueType* new_opaque(std::string name);

  // The record the textual form of the IR calls `name`: one per name, fields
  // unknown, for reading that form back.
  ProductType* named_record(const std::string& name);

  // The smallest unsigned type that holds `count` values.
  const IntType* holder_for(std::uint64_t count);

  // The common ones.
  const IntType* u8();
  const IntType* u32();
  const IntType* u64();
  const IntType* i64();
  const FloatType* f64();
  // The allocator's record, `arena`, and `⎕narrowing`.
  const ProductType* arena_type();
  const EnumType* narrowing_type();

  // The type the language calls `name`, if it names one directly.
  const Type* builtin_type(std::string_view name);

  // The same type with no unit on it.
  const Type* without_units(const Type* ty);


  // -- what a value is made of ------------------------------------------------

  // Whether a value is bytes in a place rather than values: a sum, an array
  // whose type says its shape, and a record or tuple holding either.
  bool held_in_memory(const Type* ty);

  // What a value is where it is more than one value travelling as one -- its
  // leaves, not its members.
  std::vector<const Type*> parts_of(const Type* ty);

  // Whether a value of `ty` is several values travelling as one.
  bool made_of_parts(const Type* ty);

  // What a value is in registers: a result's answer spread out.
  std::vector<const Type*> leaves_of(const Type* ty);

  // Where each member of a tuple or record begins among its parts, and how
  // many it has.
  std::vector<std::pair<std::size_t, std::size_t>> parts_within(const Type* ty);

} // namespace pl4g::ir
