// The checker's methods ported from check.py lines 6566 to 9481, defined in
// check-collect.cc.  Not a header of its own: included inside the class body of `Checker` (check.hh).

    // -- collection --------------------------------------------------------------
    // Check what a documentation comment claims about what it documents.
    void check_doc(const ast::Node* node);  // check.py:6568
    // The startup function's parameters as the entry point passes them.
    std::vector<const ir::Type*> as_the_entry_hands_over(const ast::FuncDef* node, std::span<const BoundAttr> attrs, std::span<const ir::Type* const> params);  // check.py:6615
    // Check a parameter that stands for all the arguments from there on.
    void check_several(const ast::FuncDef* node);  // check.py:6637
    // Whether the first part of a definition's name is a type this file defines.
    bool a_type_here(const ast::FuncDef* node) pre(node->held.has_value());  // check.py:6656
    // Register one function definition without looking at its body.
    Collected* collect_function(const ast::FuncDef* node, std::string_view path);  // check.py:6674
    // Register a generator: its signature, and the function its body is.
    Collected* collect_generator(const ast::FuncDef* node, std::string_view path, std::span<const BoundAttr> attrs, bool generic);  // check.py:6779
    // Make `made` reachable by the name it was defined with: at the top level, or -- written `T.name` --
    // inside the type, as any function there is.
    void put_generator(const ast::FuncDef* node, Generator* made);  // check.py:6815
    // The function a generator's body is, for the types `bound` gives its type parameters -- none, where
    // it has none.
    Generator* generator_function(const ast::FuncDef* node, std::string_view path, const ir::FuncAttrs& func_attrs, const Dict<std::string, const ir::Type*>& bound);  // check.py:6830
    // Lower a body in the middle of another, with that one set aside.
    void lower_set_aside(Collected* entry);  // check.py:6893
    // The generator `template` comes to for these types, compiled once per set of them as a generic
    // function's instances are.
    Generator* generator_instance(Generator* template_, const Dict<std::string, const ir::Type*>& bound, Span span);  // check.py:6914
    // Check a function said to be defined somewhere else, and one not.
    void check_external(const ast::FuncDef* node, const ir::FuncAttrs& attrs);  // check.py:6951
    // Report a record laid out for something else crossing a call whole.
    void not_by_value(const ir::Type* ty, Span where);  // check.py:6971
    // What each parameter of `node` is given where a call gives it nothing.
    std::vector<const ir::Value*> defaults_of(const ast::FuncDef* node, std::span<const ir::Type* const> types);  // check.py:6981
    // The value written as `param`'s default, where it is one.
    const ir::Value* default_value(const ast::Param* param, const ir::Type* ty) pre(param->default_ != nullptr);  // check.py:7010
    // Which arena parameters the answer and each parameter were made in.
    std::pair<std::optional<std::vector<int>>, std::vector<std::optional<int>>> made_in_of(const ast::FuncDef* node, std::span<const ir::Type* const> params, const ir::Type* ret);  // check.py:7032
    // Give the parameters of a body the provenance the signature says.
    void params_made_in(const ast::FuncDef* node, ir::Function* func);  // check.py:7095
    // The name of what `expr` calls, where that hands out an arena.
    std::optional<std::string> handing_callee(const ast::Expr* expr);  // check.py:7184
    // Lower `let v: T in kept = f(…)`: an arena received with an answer.
    void lower_receiving(ir::Builder& builder, const ast::VarDef* stmt) pre(stmt->made_in.has_value());  // check.py:7200
    // Where the arena a call hands out with its answer is to be kept.
    ir::Value* arena_for(const ast::Call* expr, std::string_view name);  // check.py:7231
    // What parameter `at` of a call was handed the room of: what the call wrote for it, or the arena it
    // stands in where it wrote nothing and the parameter took that; nothing where neither.
    std::optional<KeySet> arenas_handed(const ast::Call* expr, const Dict<std::int64_t, const ast::Expr*>& written, std::int64_t at);  // check.py:7247
    // What a call wrote for each parameter, by the parameter's place.
    Dict<std::int64_t, const ast::Expr*> written_for(const ast::Call* expr, ir::Function* func);  // check.py:7258
    // Hold a call to what its callee's signature says about allocators.
    void where_made(const ast::Call* expr, ir::Function* func);  // check.py:7271
    // Check a container handed to a parameter that keeps elements somewhere.
    void container_handed(const ast::Call* expr, ir::Function* func, const Dict<std::int64_t, const ast::Expr*>& written, std::int64_t at, std::optional<std::int64_t> arena_at);  // check.py:7370
    // What each reference in the answer lives as long as, where the signature says: by where it stands
    // among the answer's parts, -1 being an answer that is one reference.
    Dict<std::int64_t, ir::Borrowed> borrows_written(std::span<const ast::TypeExpr* const> params, std::span<const ir::Type* const> types, const ast::TypeExpr* ret_ref, const ir::Type* ret, Span where, bool around = false);  // check.py:7405
    // What the one reference `ret`, written `ref`, lives as long as: the parameters carrying its name, and
    // the parts of them that do -- or a name of a signature around, for a lambda or a function's type
    // (4562, 4567).
    std::optional<ir::Borrowed> borrowed_by(std::span<const ast::TypeExpr* const> params, std::span<const ir::Type* const> types, const ast::TypeExpr* ref, const ir::PtrType* ret, Span where, bool around);  // check.py:7462
    // The lifetime names the signature `node` carries on its parameters, with what tells them from another
    // signature's.
    std::tuple<std::string, NameSet> lifetime_scope(std::span<const ast::Param* const> params, const ast::Node* node);  // check.py:7500
    // What tells the lifetime names of the signature `node` from every other's: where it is written.
    std::string lifetime_key(const ast::Node* node);  // check.py:7507
    // Register a function whose types a call settles, without building one.
    Collected* collect_generic(const ast::FuncDef* node, std::string_view path, std::span<const BoundAttr> attrs, std::span<const std::string> written);  // check.py:7512
    // Record the function in the module's caches and check its signature.
    void register_special(ir::Function* func, const ast::FuncDef* node);  // check.py:7565
    // Check the build function's signature.
    void check_build_signature(ir::Function* func, const ast::FuncDef* node);  // check.py:7601
    // Check the startup function's signature.
    void check_startup_signature(ir::Function* func, const ast::FuncDef* node);  // check.py:7616
    // Check that a constructor or destructor takes nothing and returns void.
    void check_ctor_signature(ir::Function* func, const ast::FuncDef* node, std::string_view kind);  // check.py:7656
    // Check that a test takes nothing and answers whether it passed.
    void check_test_signature(ir::Function* func, const ast::FuncDef* node);  // check.py:7669

    // -- attributes --------------------------------------------------------------
    // Check a list of attributes against their declarations.
    std::vector<BoundAttr> bind_attributes(std::span<const ast::Attribute* const> nodes, AttrTarget target);  // check.py:7690
    // Match an attribute's arguments to its parameters.
    std::optional<std::vector<std::pair<std::string_view, AttrValue>>> bind_arguments(const AttrSpec* spec, const ast::Attribute* node);  // check.py:7721
    // Check one attribute argument against its declared kind.
    std::optional<AttrValue> attr_value(const AttrSpec* spec, std::string_view param_name, std::string_view kind, std::span<const std::string_view> choices, const ast::AttrArg* arg);  // check.py:7771
    // Whether the image offers this definition's symbol.
    ir::Linkage linkage_of(std::span<const BoundAttr> bound);  // check.py:7798
    // Whether a file importing this module may name the definition.
    bool is_export(std::span<const BoundAttr> bound);  // check.py:7811
    // Report a calling convention the target does not know.
    void known_convention(std::string_view name, const BoundAttr& attr);  // check.py:7815
    // Turn checked attributes into the form the IR carries.
    std::tuple<ir::FuncAttrs, ir::Linkage> function_attrs(std::span<const BoundAttr> bound);  // check.py:7840

    // -- types -------------------------------------------------------------------

    // -- types the program defines -----------------------------------------------
    // Register one type definition without working out what it is made of.
    void collect_type(const ast::Node* node, std::string_view path);  // check.py:7940
    // What a defined type is made of, worked out on first ask.
    const ir::Type* resolved(NamedType* defined);  // check.py:7987
    // The enumeration a definition makes, reporting what is wrong with it.
    const ir::Type* values_of(NamedType* defined);  // check.py:8026
    // The number one value of an enumeration is stored as.
    std::optional<std::int64_t> number_for(const ast::EnumMember* member, std::span<const std::string> names, std::span<const std::int64_t> numbers, Dict<std::int64_t, std::string>& taken, bool flag);  // check.py:8065
    // The number a value that says none is given.
    std::int64_t next_number(std::span<const std::int64_t> numbers, bool flag);  // check.py:8095
    // What holds the values: what the definition named, or the smallest unsigned type that holds every one
    // of them.
    const ir::IntType* holder_for(const ast::EnumDef* node, std::span<const std::int64_t> numbers);  // check.py:8111
    // The type a definition's parts make, reporting what is wrong with them.
    const ir::Type* parts_of(NamedType* defined);  // check.py:8130
    // The type a name stands for, where a definition gave it one.
    const ir::Type* defined_type(const ast::TypeRef* ref);  // check.py:8200
    // A defined type named with the types it is given, or without them.
    const ir::Type* named_with(NamedType* defined, const ast::TypeRef* ref);  // check.py:8226
    // The type a definition with type parameters comes to for `given`.
    const ir::Type* instance_type(NamedType* template_, std::span<const ir::Type* const> given, Span span);  // check.py:8248
    // Check a definition with type parameters where it is written.
    void check_generic_type(NamedType* template_);  // check.py:8294
    // Resolve a type written down, collection or name.
    const ir::Type* resolve_type(const ast::TypeExpr* ref);  // check.py:8319

    // -- units -------------------------------------------------------------------
    // Resolve `TYPE ¤UNIT`, checking that the type can carry one.
    const ir::Type* united_type(const ast::UnitTypeRef* ref);  // check.py:8405
    // The unit a program wrote, worked out into base units and exponents.
    const ir::Unit* unit_written(const ast::UnitRef* ref);  // check.py:8419
    // The unit `name` stands for: one a program introduced, or a builtin.
    const ir::Unit* unit_named(std::string_view name, Span span);  // check.py:8438
    // Introduce a unit, or say where one may stand.
    void define_unit(const ast::UnitDef* node);  // check.py:8450
    // Whether a value of `found` may stand where `wanted` is asked for.
    bool stands_for(const ir::Unit* found, const ir::Unit* wanted);  // check.py:8487
    // Resolve `&T` or `&mut T`, checking that a place can hold a `T`.
    const ir::Type* reference_type(const ast::RefTypeRef* ref);  // check.py:8507
    // Resolve `T⟦N⟧` or `T⟦⟧`.
    const ir::Type* array_type(const ast::ArrayTypeRef* ref);  // check.py:8532
    // Resolve `⸨T⸩` or `⸨K: V⸩`, checking that the key can be one.
    const ir::Type* collection_type(const ast::CollectionTypeRef* ref);  // check.py:8574
    // Resolve a type name, reporting an unknown one.
    const ir::Type* named_type(const ast::TypeRef* ref);  // check.py:8592

    // -- what a macro is made of -------------------------------------------------
    // Lower `⌜…⌝` in the body of a macro: the tree it holds, as a value.
    ir::Value* lower_quote(ir::Builder& builder, const ast::Quote* expr, const ir::Type* expected);  // check.py:8660
    // A number of any width as the one the tree builder takes.
    ir::Value* as_a_number(ir::Builder& builder, ir::Value* value, const ir::IntType* ty, Span span);  // check.py:8710
    // Refuse a piece of the program where a value has to outlive the compilation.
    void not_a_piece(const ir::Type* ty, Span span, std::string_view name);  // check.py:8718
    // Lower a question about a piece of the program.
    ir::Value* lower_asked(ir::Builder& builder, const ast::Call* expr, std::string_view name, const ir::Type* expected);  // check.py:8730
    // Lower `a = b` and `a ≠ b` over two pieces of the program.
    ir::Value* pieces_alike(ir::Builder& builder, const ast::Binary* expr, ir::Value* left, ir::Value* right);  // check.py:8760
    // The compiler-provided function of that name, made on first ask.
    ir::Function* syntax_builtin(std::string_view name, std::span<const ir::Type* const> params);  // check.py:8776

    // -- operators a program defines ---------------------------------------------
    // Write a definition down as an operator, where its name is one.
    void an_operator(const ast::FuncDef* node, Held what);  // check.py:8792
    // Lower a bracket pair a program defined, where this is one of those.
    ir::Value* paired(ir::Builder& builder, const ast::Expr* base, std::string_view pair, std::span<const ast::Expr* const> inside, Span span, const ir::Type* expected);  // check.py:8818
    // Whether the language's own meaning of this operator covers the operands.
    bool operator_stands(const ast::Binary* expr, const ir::Type* context);  // check.py:8834
    // The same question of an operator written before one operand.
    bool operator_stands_alone(const ast::Unary* expr);  // check.py:8848
    // What a program's own operator answers, where this expression is one.
    const ir::Type* answers_written(const ast::Expr* expr);  // check.py:8860
    // Lower an operator the language gives no meaning to.
    ir::Value* lower_fresh(ir::Builder& builder, const ast::Fresh* expr, const ir::Type* expected);  // check.py:8889
    // What a program said an operator of this arity means, if it said.
    Held operator_written(std::string_view glyph, std::int64_t arity);  // check.py:8907
    // Lower an operator a program defined, or nothing where it defined none.
    ir::Value* operator_call(ir::Builder& builder, std::string_view glyph, std::span<const ast::Expr* const> operands, Span span, const ir::Type* expected);  // check.py:8911

    // -- what a signature requires and demands -----------------------------------
    // Write down a bundle, whose lines are checked where it is applied.
    void collect_bundle(const ast::BundleDef* node, std::string_view path);  // check.py:8934
    // A requirement as the source has it, for a message about it.
    std::string as_written(const ast::Clause* clause);  // check.py:8964
    // Refuse a bundle that reaches itself, once every bundle is known.
    void check_bundle_cycles();  // check.py:8976
    // Whether `bundle` applies `wanted`, however many bundles apart.
    bool reaches(Bundle* bundle, std::string_view wanted, NameSet& seen);  // check.py:8995
    // The walk itself, with the bundle's own names already in force.
    bool reaching(Bundle* bundle, std::string_view wanted, NameSet& seen);  // check.py:9010
    // The bundle an expression applies, where it applies one.
    Bundle* bundle_named(const ast::Expr* expr);  // check.py:9025
    // Whether the operands of `expr` are types, which is what decides whether a clause holding it is a
    // requirement or a condition.
    bool over_types(const ast::Expr* expr);  // check.py:9044
    // Which levels the operands of `expr` are on: types, values, or both.
    std::set<bool> levels_of(const ast::Expr* expr);  // check.py:9055
    // Whether a type stands anywhere below `expr`, and whether a value does.
    void levels_in(const ast::Node* expr, std::set<bool>& into);  // check.py:9061
    // Whether a clause is a requirement, a condition, or neither.
    std::optional<bool> reading_of(const ast::Clause* clause);  // check.py:9114
    // Refuse a clause over types on a function with no type parameters (4647).
    bool no_requirement_here(const ast::FuncDef* node);  // check.py:9133
    // Whether every clause is of a shape that has a reading.
    bool check_clause_shapes(std::span<const ast::Clause* const> clauses);  // check.py:9150
    // Check every requirement of a signature, settling what it settles.
    bool check_clauses(std::span<const ast::Clause* const> clauses, Dict<std::string, const ir::Type*>& bound, std::string_view name, Span span);  // check.py:9185
    // Whether one requirement is met, settling the type its arrow names.
    bool met(const ast::Clause* clause, Dict<std::string, const ir::Type*>& bound, std::string_view name, Span span);  // check.py:9201
    // What a requirement's expression answers, or nothing where it cannot be written at all.
    const ir::Type* answered_by(const ast::Clause* clause, Dict<std::string, const ir::Type*>& bound, std::string_view name, Span span);  // check.py:9211
    // Whether every line of an applied bundle is met.
    bool bundle_met(Bundle* bundle, const ast::Call* expr, const Dict<std::string, const ir::Type*>& bound, std::string_view name, Span span);  // check.py:9252
    // The type an operand stands for, or nothing where it stands for a value.
    const ir::Type* type_operand(const ast::Expr* expr, const Dict<std::string, const ir::Type*>& bound);  // check.py:9303
    // The requirement rewritten so that every type operand is a name, and what type each of those names
    // stands for.
    std::optional<std::tuple<const ast::Expr*, std::vector<std::tuple<std::string, const ir::Type*>>>> dummies_for(const ast::Clause* clause, const Dict<std::string, const ir::Type*>& bound, std::string_view name);  // check.py:9319
    // What `expr` answers where each name stands for a value of its type, and nothing where it cannot be
    // written.
    const ir::Type* written_over(std::span<const std::tuple<std::string, const ir::Type*>> dummies, const ast::Expr* expr);  // check.py:9363
    // Bind what a requirement's arrow names, or check it against what is already bound.
    bool settling(const ast::Clause* clause, const ir::Type* found, Dict<std::string, const ir::Type*>& bound, std::string_view name) pre(clause->answers != nullptr);  // check.py:9397

    // -- the conditions a body carries -------------------------------------------
    // Emit the checks a function's `pre` clauses stand for.
    void lower_conditions(ir::Builder& builder, const ast::FuncDef* node, ir::Function* func, bool while_running = false);  // check.py:9428
    // Lower `⎕entry(NAME)`: what a parameter held when the function began.
    ir::Value* lower_at_entry(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected);  // check.py:9448

