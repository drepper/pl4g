// The checker's methods ported from check.py lines 3754 to 6565, defined in
// check-entry.cc.  Not a header of its own: included inside the class body of `Checker` (check.hh).

    // -- entry point -------------------------------------------------------------
    // Check every unit and lower it into the module.
    ir::Module& run(std::span<const ast::SourceUnit* const> units, bool whole_program = true);  // check.py:4251

    // -- the program's units have had them taken out -- and what -----------------
    // Lower every function of this module that one of `names` may name, and everything those reach.
    void want(const NameSet& names);  // check.py:4383
    // The module a path names, or nothing: `m`, or `m.inner` where `m` hands on a module it imported under
    // that name.
    LoadedModule* module_at(const ast::Expr* expr);  // check.py:4429
    // The module a type's name was reached through, written `m` or `m.inner`.
    LoadedModule* module_called(std::string_view path);  // check.py:4445
    // Have every module this file reads lower what `names` may name.
    void ask_the_modules(const NameSet& names);  // check.py:4453
    // Check that what every `@[closed_by(f)]` names is a function `T.f` of the type's file taking one `T`
    // by value (4686).
    void check_closers();  // check.py:4462
    // Generate the constructor that gives the top-level tables their own.
    void build_the_tables();  // check.py:4480
    // `wanted`, or `wanted` and a number where a file already took it.
    std::string unused_name(std::string_view wanted);  // check.py:4526

    // -- variables ---------------------------------------------------------------
    // What a name the compiler provides stands for, made on first ask.
    Held provided(std::string_view name);  // check.py:4537
    // The variable the environment waits in, and what fills it.
    ir::GlobalVar* environ();  // check.py:4556
    // What a definition of this file is filed under.
    std::string key(std::string_view name);  // check.py:4571
    // Record a top-level name, reporting one that is already taken.
    bool declare(std::string_view name, Span span, std::string_view path = "");  // check.py:4582
    // Register one variable defined at the top level.
    void collect_global(const ast::VarDef* node);  // check.py:4600
    // Bind what was said of the file itself, and note a system file.
    void mark_system(const ast::SourceUnit* unit);  // check.py:4701
    // Record the type of each of `names` the program did not write in full.
    void report_inferred(std::span<const std::tuple<std::string, Span>> names, const ast::TypeExpr* written);  // check.py:4719
    // Record that `name` was given `held`, where `written` does not say it.
    void record_inferred(std::string_view name, Span span, const ir::Type* held, const ast::TypeExpr* written);  // check.py:4739
    // The type `written` names, asked again. It was resolved where the definition was, and a variable
    // whose type could not be has the error type and is never asked about -- so nothing is said here, and
    // an editor is told nothing twice.
    const ir::Type* quietly_resolved(const ast::TypeExpr* written);  // check.py:4757
    // Make the singletons of a record's type: one variable each, in the image.
    void collect_singletons(const ast::TypeDef* node);  // check.py:4769
    // The singleton the compiler fills in, where this is the one (4695).
    ir::GlobalVar* filled_singleton(const ast::TypeDef* node, const ast::Field* one, std::string_view filed, bool exported);  // check.py:4819

    // -- modules -----------------------------------------------------------------
    // Bring a module into this file under the name it was given here.
    void collect_import(const ast::ModuleImport* node);  // check.py:4855
    // Put a module's exported operators in force in this file.
    void import_operators(LoadedModule* found, const ast::ModuleImport* node);  // check.py:4891
    // Read and check the file at `path`, returning what it holds.
    LoadedModule* read_module(const std::filesystem::path& path, const ast::ModuleImport* node);  // check.py:4916
    // Read and parse one module file.
    const ast::SourceUnit* read_unit(const std::filesystem::path& path, const ast::ModuleImport* node);  // check.py:4965
    // Lower something named through what it belongs to.
    ir::Value* lower_member(ir::Builder& builder, const ast::Member* expr, const ir::Type* expected);  // check.py:4994
    // Whether `name` stands for a value here rather than for a module.
    bool holds_a_record(std::string_view name);  // check.py:5064
    // Lower `VALUE.NAME`: one field of a record.
    ir::Value* field_of(ir::Builder& builder, const ast::Member* expr, const ir::Type* expected);  // check.py:5087
    // An array whose type says its shape, held in a field of a record that is somewhere, as where it is --
    // which is what a value of it is -- so that an element of it is read or written there.
    ir::Value* array_in_a_field(ir::Builder& builder, const ast::Expr* expr, bool writing);  // check.py:5140
    // One field of the record at `where`, read and nothing else.
    ir::Value* field_of_place(ir::Builder& builder, ir::Value* where, const ir::ProductType* ty, const ast::Member* expr, const ir::Type* expected);  // check.py:5168
    // Where the record `expr` names is, where it is somewhere.
    std::optional<std::tuple<ir::Value*, const ir::ProductType*>> record_at(ir::Builder& builder, const ast::Expr* expr, bool writing = false, std::span<const std::string> below = {});  // check.py:5186
    // The value of an enumeration written as `TYPE.NAME`.
    ir::Value* enum_value(NamedType* defined, const ast::Member* expr);  // check.py:5269
    // The value `expr` names of the enumeration `ty`.
    ir::Value* value_of_enum(const ir::Type* ty, const ast::Member* expr);  // check.py:5273
    // The type of a variable: the one declared, or the one its value has.
    const ir::Type* variable_type(const ast::VarDef* node);  // check.py:5288
    // `let g: ⟦⟧ = …` at the top level: an array of whatever the value is.
    const ir::Type* array_of_its_value(const ast::VarDef* node, const ast::ArrayTypeRef* written);  // check.py:5299
    // The type of an array written down, read off the writing alone.
    const ir::Type* array_written_type(const ast::ArrayLit* expr);  // check.py:5329
    // The type an expression has on its own, without a context to take one from.
    const ir::Type* type_of(const ast::Expr* expr);  // check.py:5348
    // The type naming `value` yields: what a global holds, not its address.
    const ir::Type* value_type_of(ir::Value* value);  // check.py:5402
    // The value a top-level variable is given, which must be a constant.
    ir::Value* constant_value(const ast::VarDef* node, const ir::Type* ty);  // check.py:5408
    // A set written by a macro's spread, where a dictionary is wanted: the pieces two at a time, a key and
    // what it stands for.
    const ast::DictLit* spread_paired(const ast::SetLit* written);  // check.py:5605
    // Run a call while compiling, for what it answers to be laid out in the image, and answer that as the
    // literal it would have been written as.
    const ast::Expr* worked_out_for_the_image(const ast::Call* call, const ir::Type* ty);  // check.py:5616
    // `value`, worked out while compiling, as the literal it would be written where `ty` is wanted --
    // which says what a table written empty is.
    const ast::Expr* as_literal(const comptime::Value& value, Span span, const ir::Type* ty = nullptr);  // check.py:5658
    // Check what a definition says of the image (4637, 4638).
    bool image_definition(const ast::VarDef* node, const ir::Type* declared);  // check.py:5694
    // The type something known while compiling has, read off what is written.
    Held static_type(const ast::Expr* expr);  // check.py:5725
    // What each element of an array written down says its type is, however many dimensions deep.
    std::vector<Held> static_leaves(const ast::ArrayLit* expr);  // check.py:5777
    // `&g`, `&g.field`, `&g⟦2⟧` given to a variable at the top level.
    const ir::Value* address_in_the_image(const ast::AddressOf* expr, const ir::PtrType* ty);  // check.py:5788
    // The variable at the top level `expr` names a part of, what is there, how far in, whether it may be
    // written, and what to call it -- or nothing where it is not such a place at an offset known while
    // compiling.
    std::optional<std::tuple<ir::GlobalVar*, const ir::Type*, std::int64_t, bool, std::string>> place_in_the_image(const ast::Expr* expr);  // check.py:5815
    // Whether `T.NAME` is a value of an enumeration, asked quietly.
    bool names_an_enum(const ast::Member* expr);  // check.py:5857
    // Text written down, as the constant a `str` in the image is.
    const ir::PartsConst* text_constant(std::string_view text);  // check.py:5864
    // A variable of the image nothing names, holding `value`: a run, a table, or what a body put `in
    // ⎕static` -- where a table may be put in, one that may be written.
    ir::GlobalVar* image_variable(const ir::Type* ty, const ir::Value* value, bool mutable_ = false);  // check.py:5872
    // A set or a dictionary in the image: the table a program would build.
    const ir::Value* table_constant(const ast::VarDef* node, const ir::Type* ty, std::span<const std::tuple<const ast::Expr*, const ast::Expr*>> written);  // check.py:5882
    // An empty set or dictionary of the heap, laid out in the image.
    const ir::Value* empty_table_constant(const ir::Type* ty);  // check.py:5950
    // What a key is to the table: the word a program's key is widened to, or the bytes of text.
    Held key_bits(const ir::Value* key);  // check.py:5984
    // A record written out at the top level, as the bytes it comes to.
    const ir::Value* constant_record(const ast::VarDef* node, const ir::ProductType* ty);  // check.py:6010
    // A call at the top level that does not write a value of `ty` out: a value of another type written out
    // is a mismatch, and anything else -- a call of a function -- is what the image cannot hold yet.
    void not_this_type(const ast::VarDef* node, const ir::Type* ty);  // check.py:6029
    // A value of a sum written out at the top level, as the bytes it comes to.
    const ir::Value* constant_sum(const ast::VarDef* node, const ir::SumType* ty);  // check.py:6044
    // Read a variable at the top level.
    ir::Value* read_global(ir::Builder& builder, ir::GlobalVar* var, Span span);  // check.py:6086
    // Every element of an array written down, in the order they are laid out.
    std::optional<std::vector<const ir::Value*>> constant_elements(const ast::VarDef* node, const ir::ArrayType* ty, const ast::ArrayLit* written, std::span<const std::optional<std::uint64_t>> shape);  // check.py:6107
    // The value of an enumeration a `TYPE.NAME` names, outside a body.
    const ir::EnumConst* enum_value_of(const ast::Member* expr);  // check.py:6134
    // Report a literal of a kind the declared type cannot hold.
    void wrong_initializer(const ast::VarDef* node, const ir::Type* ty, std::string_view found);  // check.py:6146
    // Check a suffixed literal against the type the variable was declared.
    bool literal_matches(const ast::VarDef* node, const ir::Type* ty);  // check.py:6157

    // -- expectations ------------------------------------------------------------
    // What a construct says about the diagnostics it raises.
    std::vector<Expected> expected_numbers(std::span<const BoundAttr> attrs);  // check.py:6171
    // Put what a construct said about its diagnostics in force.
    Expectation* begin_expecting(std::span<const Expected> pairs);  // check.py:6190
    // Take them out of force, without yet deciding whether they were met.
    void end_expecting(Expectation* expectation);  // check.py:6198
    // Report the assertions nothing met, and say whether what was absorbed prevents the construct from
    // being compiled.
    bool settle_expecting(Expectation* expectation, std::span<const Expected> pairs);  // check.py:6203

    // -- scopes ------------------------------------------------------------------
    // Enter a nested scope.
    void push_scope();  // check.py:6216
    // Leave the innermost scope, reporting values nothing read.
    void pop_scope(std::optional<Span> left_at = {});  // check.py:6221
    // Report a value nothing read before it went out of reach.
    void report_unused(Local* local);  // check.py:6243
    // Report a variable or a parameter written `mut` that nothing changed (4931): neither given another
    // value, nor written in part, nor lent so that something else may. An arena changes when it is given
    // back, which the compiler does whatever is written. A table used whole may be where a writable one is
    // wanted, its `mut` being part of its type, and is not reported. The compiler's own names -- a
    // macro's, a generator's written into a loop -- are its own business.
    void report_needless_mut(Local* local);  // check.py:6266
    // Decide whether what a definition said it raises was met.
    void settle_local(Local* local);  // check.py:6292
    // Bind a name in the innermost scope, reporting one already bound there.
    void bind_local(std::string_view name, ir::Value* value, Span span, bool mutable_ = false, Span value_span = invalid_span, bool is_parameter = false, ir::Builder* builder = nullptr, const ir::Type* placed_as = nullptr);  // check.py:6298
    // A variable given a value that must be closed owes the close from here.
    void lifetime_begins(Local* local, const ir::Type* held);  // check.py:6384
    // Read a variable holding a value that must be closed as a value: the value is moved and the
    // variable's lifetime ends. Whether it still held one to move.
    bool moved(Local* local, Span span, std::string_view how);  // check.py:6404
    // Say, once for each place, that `local` is read after its end.
    void told_ended(Local* local, Span span);  // check.py:6430
    // Check that every variable in `scopes` holding a value that must be closed was closed or moved on,
    // here where they are left.
    void all_closed(std::span<const Dict<std::string, Local*>> scopes, Span span);  // check.py:6441
    // Refuse moving a value that must be closed out of anything but a variable, and putting one inside
    // something, where that is what `expr` is doing.
    ir::Value* not_moved_out(const ast::Expr* expr, ir::Value* value);  // check.py:6463
    // Report a name nothing here stands for, saying which nothing it is.
    void report_undefined(std::string_view name, Span span);  // check.py:6493
    // The type a name has, whether it stands for a value or for a place.
    const ir::Type* held_by(Local* local);  // check.py:6506
    // The innermost binding of `name`, if there is one.
    Local* find_local(std::string_view name);  // check.py:6512
    // Write down what a name turned out to be, where anything is listening.
    void note(Span span, std::string_view kind, std::string_view name, std::string_view detail, Span defined = invalid_span, std::optional<std::string> doc = {});  // check.py:6520
    // Resolve a name: the innermost binding first, then the top level.
    ir::Value* lookup(const ast::NameRef* ref);  // check.py:6532
