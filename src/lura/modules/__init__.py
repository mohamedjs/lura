"""Bounded contexts. Import a module only through its package, never its internals.

``test_architecture.py`` enforces that: a module may import another module's
package (``from lura.modules.brain import BrainService``) but not its
``infrastructure`` or ``application`` files.
"""
