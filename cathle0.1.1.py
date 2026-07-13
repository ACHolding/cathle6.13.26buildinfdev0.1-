#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, infer_types=True
"""
cathle0.1.1.py — N64 emulator (clean-room Python monolith)
Engine: cathle
Single-file Python 3.14 target — Tkinter only (PIL optional for framebuffer)

cathle 0.1.1 — branded release
PJ64-Legacy v1.6.4 interpreter algorithms ported for SM64 compatibility
files: ON — ROM loaded from disk, full file I/O enabled

Features:
- R4300i interpreter with PJ64-Legacy unaligned load/store, FPU rounding, TLB
- ROM browser with Tk GUI
- files=ON: ROM loaded from disk, full file I/O enabled
"""

from __future__ import annotations

import base64
import hashlib
import math
import os
import platform
import struct
import sys
import time
import random
import io

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Callable

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_ROM_DIR = os.path.join(_SCRIPT_DIR, "Roms")
_ROM_SCAN_MAX_FILES = 512

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ImportError:
    tk = None
    filedialog = None
    messagebox = None
    ttk = None

APP_NAME = "cathle 0.1.1"
VERSION = "0.1.1"
ENGINE_NAME = "cathle"
PYTHON_TARGET = "3.14"
WINDOW_TITLE = "cathle 0.1.1"

ROM_EXTENSIONS = (".z64", ".v64", ".n64", ".rom", ".bin")
ROM_BROWSER_COLUMNS = (
    ("file_name", "File Name", 220),
    ("internal_name", "Internal Name", 180),
    ("good_name", "Good Name", 200),
    ("status", "Status", 90),
    ("rom_size", "Rom Size", 90),
)

@dataclass(frozen=True)
class CathlePluginSlot:
    name: str
    role: str

def cathle_plugin_slots_monolith() -> Tuple[CathlePluginSlot, ...]:
    return (
        CathlePluginSlot("Gfx", "RDP display lists"),
        CathlePluginSlot("Audio", "AI DMA drain"),
        CathlePluginSlot("RSP", "SP DMA + HLE"),
        CathlePluginSlot("Controller", "SI PIF + keyboard"),
    )

class CathleSystemFacade:
    __slots__ = ("_core",)
    def __init__(self, core: "ACsN64Core") -> None:
        self._core = core
    @property
    def m_Cpu(self) -> "CPUCore":
        return self._core.cpu
    @property
    def m_Bus(self) -> "DeviceBus":
        return self._core.bus
    @property
    def m_RDRAM(self) -> bytearray:
        return self._core.rdram
    @property
    def m_CartRom(self) -> bytearray:
        return self._core.rom
    @property
    def m_RSP_DMEM(self) -> bytearray:
        return self._core.rsp_dmem
    @property
    def m_RSP_IMEM(self) -> bytearray:
        return self._core.rsp_imem
    @property
    def m_PIF_RAM(self) -> bytearray:
        return self._core.pif_ram
    @property
    def m_PluginSlots(self) -> Tuple[CathlePluginSlot, ...]:
        return self._core.cathle_plugin_slots
    def step_cpu_instruction(self) -> None:
        self._core.cpu.step()
    def run_rsp_hle(self) -> None:
        self._core.process_rsp()
    def run_rdp_hle(self) -> None:
        self._core.process_rdp()
    def run_ai_hle(self) -> None:
        self._core.process_audio()

CATHLE_WIN_GRAY = "#c0c0c0"
CATHLE_WIN_FACE = "#c0c0c0"
CATHLE_BTN_FACE = "#c0c0c0"
CATHLE_BTN_HIGHLIGHT = "#ffffff"
CATHLE_BTN_SHADOW = "#808080"
CATHLE_PANEL_WHITE = "#ffffff"
CATHLE_TEXT = "#000000"
CATHLE_SPLASH_GRAY = "#808080"
CATHLE_VIEWPORT_BORDER = "#808080"
CATHLE_LIST_SEL_BG = "#000080"
CATHLE_LIST_SEL_FG = "#ffffff"
CATHLE_LIST_ALT = "#f0f0f0"

BG_COLOR = CATHLE_WIN_GRAY
PANEL_COLOR = CATHLE_BTN_FACE
TEXT_COLOR = CATHLE_TEXT
ACCENT_BLUE = CATHLE_TEXT
TERMINAL_GREEN = "#008000"
STATUS_RED = "#800000"
WHITE = CATHLE_PANEL_WHITE

def _cathle_ui_fonts() -> Tuple[Tuple[str, int], Tuple[str, int], Tuple[str, int, str]]:
    if platform.system() == "Darwin":
        return ("Tahoma", 11), ("Courier New", 11), ("Tahoma", 11, "bold")
    if platform.system() == "Windows":
        return ("MS Sans Serif", 8), ("Courier New", 9), ("MS Sans Serif", 8, "bold")
    return ("TkDefaultFont", 9), ("Courier New", 9), ("TkDefaultFont", 9, "bold")

UI_FONT, UI_FONT_MONO, UI_FONT_BOLD = _cathle_ui_fonts()

RDRAM_SIZE = 8 * 1024 * 1024
RSP_DMEM_SIZE = 0x1000
RSP_IMEM_SIZE = 0x1000
PIF_RAM_SIZE = 0x40

VI_ORIGIN_REG = 0x04400004
VI_WIDTH_REG = 0x04400008

MASK_8 = 0xFF
MASK_16 = 0xFFFF
MASK_32 = 0xFFFFFFFF
MASK_64 = 0xFFFFFFFFFFFFFFFF

CP0_INDEX = 0
CP0_RANDOM = 1
CP0_ENTRYLO0 = 2
CP0_ENTRYLO1 = 3
CP0_CONTEXT = 4
CP0_PAGEMASK = 5
CP0_WIRED = 6
CP0_BADVADDR = 8
CP0_COUNT = 9
CP0_ENTRYHI = 10
CP0_COMPARE = 11
CP0_STATUS = 12
CP0_CAUSE = 13
CP0_EPC = 14
CP0_PRID = 15
CP0_CONFIG = 16
CP0_LLADDR = 17
CP0_ERROREPC = 30

FCR31_COND_BIT = 23

FCR31_CAUSE_INEXACT = 0x01
FCR31_CAUSE_UNDERFLOW = 0x02
FCR31_CAUSE_OVERFLOW = 0x04
FCR31_CAUSE_DIVBYZERO = 0x08
FCR31_CAUSE_INVALID = 0x10
FCR31_CAUSE_UNIMPLEMENTED = 0x20

def u8(v: int) -> int: return v & MASK_8
def u16(v: int) -> int: return v & MASK_16
def u32(v: int) -> int: return v & MASK_32
def u64(v: int) -> int: return v & MASK_64

def sign8(v: int) -> int:
    v &= MASK_8
    return v - 0x100 if v & 0x80 else v

def sign16(v: int) -> int:
    v &= MASK_16
    return v - 0x10000 if v & 0x8000 else v

def sign32(v: int) -> int:
    v &= MASK_32
    return v - 0x100000000 if v & 0x80000000 else v

def sign64(v: int) -> int:
    v &= MASK_64
    return v - 0x10000000000000000 if v & 0x8000000000000000 else v

def sx8_to_64(v: int) -> int: return u64(sign8(v))
def sx16_to_64(v: int) -> int: return u64(sign16(v))
def sx32_to_64(v: int) -> int: return u64(sign32(v))

def be32(data: bytearray | bytes, offset: int) -> int:
    if offset < 0 or offset + 3 >= len(data): return 0
    return struct.unpack_from(">I", data, offset)[0]

def put_be32(data: bytearray, offset: int, value: int) -> None:
    if offset < 0 or offset + 3 >= len(data): return
    struct.pack_into(">I", data, offset, value & MASK_32)

def be64(data: bytearray | bytes, offset: int) -> int:
    if offset < 0 or offset + 7 >= len(data): return 0
    return struct.unpack_from(">Q", data, offset)[0]

def put_be64(data: bytearray, offset: int, value: int) -> None:
    if offset < 0 or offset + 7 >= len(data): return
    struct.pack_into(">Q", data, offset, value & MASK_64)

def rdram_rgb5551_to_ppm(rdram: bytearray, origin: int, width: int, height: int) -> bytes | None:
    origin &= 0xFFFFFF
    width = max(1, min(width, 320))
    height = max(1, min(height, 240))
    stride = width * 2
    need = origin + stride * height
    if origin < 0 or need > len(rdram):
        return None
    header = f"P6\n{width} {height}\n255\n".encode("ascii")
    out = bytearray(width * height * 3)
    mv = memoryview(rdram)
    o = 0
    for y in range(height):
        row = origin + y * stride
        for x in range(0, stride, 2):
            px = (mv[row + x] << 8) | mv[row + x + 1]
            out[o] = ((px >> 11) & 0x1F) << 3
            out[o + 1] = ((px >> 6) & 0x1F) << 3
            out[o + 2] = ((px >> 1) & 0x1F) << 3
            o += 3
    return header + bytes(out)

def f32_to_bits(value: float) -> int:
    return struct.unpack(">I", struct.pack(">f", float(value)))[0]

def bits_to_f32(value: int) -> float:
    return struct.unpack(">f", struct.pack(">I", value & MASK_32))[0]

def f64_to_bits(value: float) -> int:
    return struct.unpack(">Q", struct.pack(">d", float(value)))[0]

def bits_to_f64(value: int) -> float:
    return struct.unpack(">d", struct.pack(">Q", value & MASK_64))[0]

def normalize_commercial_entry(addr: int) -> int:
    addr = u32(addr)
    if addr == 0 or addr == MASK_32:
        return 0x80000400
    hi = addr >> 24
    if hi in (0x80, 0xA0, 0xB0):
        if hi == 0xB0:
            return 0x80000000 | (addr & 0x1FFFFFFF)
        return addr
    if addr < RDRAM_SIZE:
        return 0x80000000 | addr
    if hi == 0 and addr < 0x04000000:
        return 0x80000000 | addr
    return addr

def seed_commercial_pif_ram(pif: bytearray) -> None:
    pif[:] = b"\x00" * PIF_RAM_SIZE
    for i in range(4):
        pif[i * 4] = 0x01
    for i in range(4):
        pif[0x20 + i * 4] = 0x00
        pif[0x21 + i * 4] = 0x00
        pif[0x22 + i * 4] = 0x00
        pif[0x23 + i * 4] = 0x00
    pif[0x18] = 0x00
    pif[0x19] = 0x04

Z64_BIG_ENDIAN_MAGIC = b"\x80\x37\x12\x40"
V64_MAGIC = b"\x37\x80\x40\x12"
N64_LE_MAGIC = b"\x40\x12\x37\x80"
_CART_SIGS = (Z64_BIG_ENDIAN_MAGIC, V64_MAGIC, N64_LE_MAGIC)

def strip_documentation_header_if_present(data: bytearray) -> None:
    if len(data) < 4:
        return
    for _ in range(4):
        if len(data) >= 4 and data[0:4] in _CART_SIGS:
            return
        search_cap = min(len(data), 16 * 1024 * 1024)
        found = False
        for off in (4096, 2048, 512):
            if off + 4 <= search_cap and data[off : off + 4] in _CART_SIGS:
                del data[:off]
                found = True
                break
        if not found:
            return

def apply_ultra64_cart_header_defaults(data: bytearray) -> None:
    if len(data) < 0x40:
        data.extend(b"\x00" * (0x40 - len(data)))
    if data[0:4] not in _CART_SIGS:
        return
    if data[0:4] != Z64_BIG_ENDIAN_MAGIC:
        return
    if be32(data, 0x04) == 0:
        put_be32(data, 0x04, 0x00000F48)
    boot = be32(data, 0x08)
    if boot == 0 or boot == MASK_32:
        put_be32(data, 0x08, 0x80000400)
    if be32(data, 0x0C) == 0:
        put_be32(data, 0x0C, 0x0000144B)
    title_region = data[0x20:0x34]
    if not any(title_region):
        pat = b"Ultra 64\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
        data[0x20:0x34] = pat[:20].ljust(20, b"\x00")

def normalize_rom_bytes(data: bytearray) -> bytearray:
    data = bytearray(data)
    strip_documentation_header_if_present(data)
    if len(data) < 4:
        return data
    magic = data[0:4]
    if magic == Z64_BIG_ENDIAN_MAGIC:
        apply_ultra64_cart_header_defaults(data)
        return data
    if magic == V64_MAGIC:
        for i in range(0, len(data) - 1, 2):
            data[i], data[i + 1] = data[i + 1], data[i]
        apply_ultra64_cart_header_defaults(data)
        return data
    if magic == N64_LE_MAGIC:
        for i in range(0, len(data) - 3, 4):
            data[i], data[i + 3] = data[i + 3], data[i]
            data[i + 1], data[i + 2] = data[i + 2], data[i + 1]
        apply_ultra64_cart_header_defaults(data)
        return data
    return data

def default_rom_directory() -> str:
    try:
        os.makedirs(_DEFAULT_ROM_DIR, exist_ok=True)
        return _DEFAULT_ROM_DIR
    except OSError:
        return _SCRIPT_DIR

_lwl_mask = [0, 0xFF, 0xFFFF, 0xFFFFFF]
_lwl_shift = [0, 8, 16, 24]
_lwr_mask = [0xFFFFFF00, 0xFFFF0000, 0xFF000000, 0]
_lwr_shift = [24, 16, 8, 0]

_swl_mask = [0, 0xFF000000, 0xFFFF0000, 0xFFFFFF00]
_swl_shift = [0, 8, 16, 24]
_swr_mask = [0x00FFFFFF, 0x0000FFFF, 0x000000FF, 0x00000000]
_swr_shift = [24, 16, 8, 0]

_ldl_mask = [0, 0xFF, 0xFFFF, 0xFFFFFF, 0xFFFFFFFF, 0xFFFFFFFFFF, 0xFFFFFFFFFFFF, 0xFFFFFFFFFFFFFF]
_ldl_shift = [0, 8, 16, 24, 32, 40, 48, 56]
_ldr_mask = [0xFFFFFFFFFFFFFF00, 0xFFFFFFFFFFFF0000, 0xFFFFFFFFFF000000, 0xFFFFFFFF00000000, 0xFFFFFF0000000000, 0xFFFF000000000000, 0xFF00000000000000, 0]
_ldr_shift = [56, 48, 40, 32, 24, 16, 8, 0]
_sdl_mask = [0, 0xFF00000000000000, 0xFFFF000000000000, 0xFFFFFF0000000000, 0xFFFFFFFF00000000, 0xFFFFFFFFFF000000, 0xFFFFFFFFFFFF0000, 0xFFFFFFFFFFFFFF00]
_sdl_shift = [0, 8, 16, 24, 32, 40, 48, 56]
_sdr_mask = [0x00FFFFFFFFFFFFFF, 0x0000FFFFFFFFFFFF, 0x000000FFFFFFFFFF, 0x00000000FFFFFFFF, 0x0000000000FFFFFF, 0x000000000000FFFF, 0x00000000000000FF, 0x0000000000000000]
_sdr_shift = [56, 48, 40, 32, 24, 16, 8, 0]

PRIMARY_OPS = {
    0x00: "SPECIAL", 0x01: "REGIMM", 0x02: "J", 0x03: "JAL",
    0x04: "BEQ", 0x05: "BNE", 0x06: "BLEZ", 0x07: "BGTZ",
    0x08: "ADDI", 0x09: "ADDIU", 0x0A: "SLTI", 0x0B: "SLTIU",
    0x0C: "ANDI", 0x0D: "ORI", 0x0E: "XORI", 0x0F: "LUI",
    0x10: "COP0", 0x11: "COP1", 0x12: "COP2", 0x13: "COP3",
    0x14: "BEQL", 0x15: "BNEL", 0x16: "BLEZL", 0x17: "BGTZL",
    0x18: "DADDI", 0x19: "DADDIU", 0x1A: "LDL", 0x1B: "LDR",
    0x20: "LB", 0x21: "LH", 0x22: "LWL", 0x23: "LW",
    0x24: "LBU", 0x25: "LHU", 0x26: "LWR", 0x27: "LWU",
    0x28: "SB", 0x29: "SH", 0x2A: "SWL", 0x2B: "SW",
    0x2C: "SDL", 0x2D: "SDR", 0x2E: "SWR", 0x2F: "CACHE",
    0x30: "LL", 0x31: "LWC1", 0x32: "LWC2", 0x33: "LWC3",
    0x34: "LLD", 0x35: "LDC1", 0x36: "LDC2", 0x37: "LD",
    0x38: "SC", 0x39: "SWC1", 0x3A: "SWC2", 0x3B: "SWC3",
    0x3C: "SCD", 0x3D: "SDC1", 0x3E: "SDC2", 0x3F: "SD",
}

SPECIAL_OPS = {
    0x00: "SLL", 0x02: "SRL", 0x03: "SRA", 0x04: "SLLV",
    0x06: "SRLV", 0x07: "SRAV",     0x08: "JR", 0x09: "JALR",
    0x0A: "MOVZ", 0x0B: "MOVN",
    0x0C: "SYSCALL", 0x0D: "BREAK", 0x0F: "SYNC",
    0x10: "MFHI", 0x11: "MTHI", 0x12: "MFLO", 0x13: "MTLO",
    0x14: "DSLLV", 0x16: "DSRLV", 0x17: "DSRAV",
    0x18: "MULT", 0x19: "MULTU", 0x1A: "DIV", 0x1B: "DIVU",
    0x1C: "DMULT", 0x1D: "DMULTU", 0x1E: "DDIV", 0x1F: "DDIVU",
    0x20: "ADD", 0x21: "ADDU", 0x22: "SUB", 0x23: "SUBU",
    0x24: "AND", 0x25: "OR", 0x26: "XOR", 0x27: "NOR",
    0x2A: "SLT", 0x2B: "SLTU", 0x2C: "DADD", 0x2D: "DADDU",
    0x2E: "DSUB", 0x2F: "DSUBU",
    0x30: "TGE", 0x31: "TGEU", 0x32: "TLT", 0x33: "TLTU",
    0x34: "TEQ", 0x36: "TNE",
    0x38: "DSLL", 0x3A: "DSRL", 0x3B: "DSRA",
    0x3C: "DSLL32", 0x3E: "DSRL32", 0x3F: "DSRA32",
}

REGIMM_OPS = {
    0x00: "BLTZ", 0x01: "BGEZ", 0x02: "BLTZL", 0x03: "BGEZL",
    0x08: "TGEI", 0x09: "TGEIU", 0x0A: "TLTI", 0x0B: "TLTIU",
    0x0C: "TEQI", 0x0E: "TNEI",
    0x10: "BLTZAL", 0x11: "BGEZAL", 0x12: "BLTZALL", 0x13: "BGEZALL",
}

COP0_RS = {
    0x00: "MFC0", 0x01: "DMFC0", 0x02: "CFC0", 0x04: "MTC0",
    0x05: "DMTC0", 0x06: "CTC0", 0x08: "BC0", 0x10: "COP0_CO",
}

COP0_CO = {
    0x01: "TLBR", 0x02: "TLBWI", 0x06: "TLBWR", 0x08: "TLBP",
    0x18: "ERET",
}

COP1_RS = {
    0x00: "MFC1", 0x01: "DMFC1", 0x02: "CFC1", 0x04: "MTC1",
    0x05: "DMTC1", 0x06: "CTC1", 0x08: "BC1",
    0x10: "S", 0x11: "D", 0x14: "W", 0x15: "L",
}

COP1_FUNCT = {
    0x00: "ADD", 0x01: "SUB", 0x02: "MUL", 0x03: "DIV",
    0x04: "SQRT", 0x05: "ABS", 0x06: "MOV", 0x07: "NEG",
    0x08: "ROUND.L", 0x09: "TRUNC.L", 0x0A: "CEIL.L", 0x0B: "FLOOR.L",
    0x0C: "ROUND.W", 0x0D: "TRUNC.W", 0x0E: "CEIL.W", 0x0F: "FLOOR.W",
    0x20: "CVT.S", 0x21: "CVT.D", 0x24: "CVT.W", 0x25: "CVT.L",
    0x30: "C.F", 0x31: "C.UN", 0x32: "C.EQ", 0x33: "C.UEQ",
    0x34: "C.OLT", 0x35: "C.ULT", 0x36: "C.OLE", 0x37: "C.ULE",
    0x38: "C.SF", 0x39: "C.NGLE", 0x3A: "C.SEQ", 0x3B: "C.NGL",
    0x3C: "C.LT", 0x3D: "C.NGE", 0x3E: "C.LE", 0x3F: "C.NGT",
}

class N64Header:
    def __init__(self, data: bytearray):
        if len(data) >= 0x40:
            self.pi_bsd_dom1_lat = data[0]
            self.pi_bsd_dom1_pwd = data[1]
            self.pi_bsd_dom1_pgs = data[2]
            self.pi_bsd_dom1_rls = data[3]
            self.clock_rate = be32(data, 0x04)
            self.boot_address = be32(data, 0x08)
            self.release = be32(data, 0x0C)
            self.crc1 = be32(data, 0x10)
            self.crc2 = be32(data, 0x14)
            self.title = data[0x20:0x34].decode('ascii', 'ignore').strip('\x00').strip()
            self.cart_id = data[0x3C:0x3E].decode('ascii', 'ignore')
        else:
            self.clock_rate = 0
            self.boot_address = 0x80000400
            self.release = 0
            self.crc1 = 0
            self.crc2 = 0
            self.title = "UNKNOWN"
            self.cart_id = "??"

@dataclass
class TLBEntry:
    mask: int = 0
    vpn2: int = 0
    g: bool = False
    asid: int = 0
    pfn0: int = 0
    c0: int = 0
    d0: bool = False
    v0: bool = False
    pfn1: int = 0
    c1: int = 0
    d1: bool = False
    v1: bool = False

class N64Opcode:
    __slots__ = ("word", "op", "rs", "rt", "rd", "sa", "funct", "imm", "simm", "target")
    def __init__(self, word: int):
        self.word = word & MASK_32
        self.op = (self.word >> 26) & 0x3F
        self.rs = (self.word >> 21) & 0x1F
        self.rt = (self.word >> 16) & 0x1F
        self.rd = (self.word >> 11) & 0x1F
        self.sa = (self.word >> 6) & 0x1F
        self.funct = self.word & 0x3F
        self.imm = self.word & MASK_16
        self.simm = sign16(self.imm)
        self.target = self.word & 0x03FFFFFF

    def target_addr(self, pc: int) -> int:
        return u32(((pc + 4) & 0xF0000000) | (self.target << 2))

    def branch_addr(self, pc: int) -> int:
        return u32(pc + 4 + (self.simm << 2))

class DeviceBus:
    def __init__(self, core: "ACsN64Core"):
        self.core = core
        self.regs: Dict[int, int] = {}
        self.hw_interrupts: int = 0
        self.reset()

    def reset(self):
        self.regs.clear()
        self.hw_interrupts = 0
        self.regs[VI_ORIGIN_REG] = 0
        self.regs[VI_WIDTH_REG] = 320
        self.regs[0x04400010] = 0x3FF
        self.regs[0x04600010] = 0
        self.regs[0x0450000C] = 0
        self.regs[0x04800018] = 0
        self.regs[0x04040010] = 1
        self.core._vi_origin_set = False

    def v_to_p(self, addr: int) -> int:
        addr &= MASK_32
        segment = addr >> 29
        if segment in (0b100, 0b101):
            return addr & 0x1FFFFFFF
        tlb = self.core.cpu.tlb
        asid = self.core.cpu.cp0[CP0_ENTRYHI] & 0xFF
        vpn2 = (addr >> 13) & 0x7FFFF
        mask_bits = 0
        for entry in tlb:
            if entry.vpn2 == vpn2 and (entry.g or entry.asid == asid):
                even_odd = (addr >> 12) & 1
                if even_odd == 0:
                    if entry.v0:
                        return (entry.pfn0 << 12) | (addr & 0xFFF)
                else:
                    if entry.v1:
                        return (entry.pfn1 << 12) | (addr & 0xFFF)
        return addr & 0x1FFFFFFF

    def read_u8(self, addr: int) -> int:
        p = self.v_to_p(addr)
        if 0 <= p < RDRAM_SIZE:
            return self.core.rdram[p]
        if 0x10000000 <= p < 0x10000000 + len(self.core.rom):
            return self.core.rom[p - 0x10000000]
        if 0x1FC007C0 <= p < 0x1FC007C0 + PIF_RAM_SIZE:
            return self.core.pif_ram[p - 0x1FC007C0]
        return 0

    def read_u16(self, addr: int) -> int:
        p = self.v_to_p(addr)
        if 0 <= p < RDRAM_SIZE - 1:
            return (self.core.rdram[p] << 8) | self.core.rdram[p + 1]
        return 0

    def read_u32(self, addr: int) -> int:
        p_addr = self.v_to_p(addr)
        if 0x00000000 <= p_addr <= RDRAM_SIZE - 4:
            return be32(self.core.rdram, p_addr)
        if 0x04000000 <= p_addr <= 0x04001000 - 4:
            return be32(self.core.rsp_dmem, p_addr - 0x04000000)
        if 0x04001000 <= p_addr <= 0x04002000 - 4:
            return be32(self.core.rsp_imem, p_addr - 0x04001000)
        if 0x04040000 <= p_addr <= 0x048FFFFF:
            aligned = p_addr & ~3
            if aligned == 0x04300004:
                return self.hw_interrupts
            return self.regs.get(aligned, 0)
        rom_len = len(self.core.rom)
        roff = p_addr - 0x10000000
        if 0 <= roff <= rom_len - 4:
            return be32(self.core.rom, roff)
        return 0

    def read_u64(self, addr: int) -> int:
        hi = self.read_u32(addr)
        lo = self.read_u32(addr + 4)
        return ((hi << 32) | lo) & MASK_64

    def write_u8(self, addr: int, val: int):
        p = self.v_to_p(addr)
        if 0 <= p < RDRAM_SIZE:
            self.core.rdram[p] = val & MASK_8
        elif 0x1FC007C0 <= p < 0x1FC007C0 + PIF_RAM_SIZE:
            self.core.pif_ram[p - 0x1FC007C0] = val & MASK_8

    def write_u16(self, addr: int, val: int):
        p = self.v_to_p(addr)
        if 0 <= p < RDRAM_SIZE - 1:
            val &= MASK_16
            self.core.rdram[p] = (val >> 8) & MASK_8
            self.core.rdram[p + 1] = val & MASK_8

    def write_u32(self, addr: int, val: int):
        p_addr = self.v_to_p(addr)
        if 0x00000000 <= p_addr <= RDRAM_SIZE - 4:
            put_be32(self.core.rdram, p_addr, val)
        elif 0x04000000 <= p_addr <= 0x04001000 - 4:
            put_be32(self.core.rsp_dmem, p_addr - 0x04000000, val)
        elif 0x04001000 <= p_addr <= 0x04002000 - 4:
            put_be32(self.core.rsp_imem, p_addr - 0x04001000, val)
        elif 0x04040000 <= p_addr <= 0x048FFFFF:
            aligned = p_addr & ~3
            if aligned in (0x04300004, 0x04040010):
                self.handle_mmio(aligned, val)
            else:
                self.regs[aligned] = val
                self.handle_mmio(aligned, val)
                if aligned == VI_ORIGIN_REG and (val & 0xFFFFFF) != 0:
                    self.core._vi_origin_set = True

    def write_u64(self, addr: int, val: int):
        val &= MASK_64
        self.write_u32(addr, (val >> 32) & MASK_32)
        self.write_u32(addr + 4, val & MASK_32)

    def handle_mmio(self, addr: int, val: int):
        if addr == 0x0460000C:
            self.core.trigger_pi_dma()
            self.regs[0x04600010] = 0
        elif addr == 0x04040008:
            self.core.trigger_sp_dma(to_rsp=True)
        elif addr == 0x0404000C:
            self.core.trigger_sp_dma(to_rsp=False)
        elif addr == 0x04040010:
            if val & 1:
                self.regs[addr] &= ~1
            if val & 2:
                self.regs[addr] |= 1
            if val & 4:
                self.regs[addr] &= ~2
            if val & 8:
                self.hw_interrupts &= ~0x01
            if val & 16:
                self.hw_interrupts |= 0x01
            if not (self.regs[addr] & 1):
                self.core.process_rsp()
        elif addr == 0x04300004:
            self.hw_interrupts &= ~val
        elif addr == 0x04100004:
            self.core.process_rdp()
        elif addr == 0x04500004:
            self.core.process_audio()
        elif addr == 0x04800004:
            self.core.trigger_si_dma(read_pif=True)
        elif addr == 0x04800010:
            self.core.trigger_si_dma(read_pif=False)

class CPUCore:
    def __init__(self, core: "ACsN64Core"):
        self.core = core
        self.gpr = [0] * 32
        self.fpr = [0] * 32
        self.cp0 = [0] * 32
        self.fcr0 = 0x00000511
        self.fcr31 = 0
        self.hi = 0
        self.lo = 0
        self.pc = 0
        self.next_pc = 4
        self.llbit = False
        self.lladdr = 0
        self.tlb: List[TLBEntry] = [TLBEntry() for _ in range(32)]
        self.reset()

    def reset(self):
        self.gpr = [0] * 32
        self.fpr = [0] * 32
        self.cp0 = [0] * 32
        self.fcr0 = 0x00000511
        self.fcr31 = 0
        self.hi = 0
        self.lo = 0
        self.pc = 0
        self.next_pc = 4
        self.cp0[CP0_PRID] = 0x00000B00
        self.cp0[CP0_STATUS] = 0x34000000
        self.cp0[CP0_CONFIG] = 0x0006E463
        self.cp0[CP0_WIRED] = 0
        self.llbit = False
        self.lladdr = 0
        self.tlb = [TLBEntry() for _ in range(32)]

    def step(self):
        mi_hw = self.core.bus.hw_interrupts
        if mi_hw & 0x3F and (self.cp0[CP0_STATUS] & 1) and not (self.cp0[CP0_STATUS] & 0xFE):
            im = (self.cp0[CP0_STATUS] >> 8) & 0x3F
            ip = 0
            if mi_hw & (0x01 | 0x10):
                ip |= 0x04
            if mi_hw & 0x02:
                ip |= 0x08
            if mi_hw & 0x04:
                ip |= 0x10
            if mi_hw & (0x08 | 0x20):
                ip |= 0x20
            if ip & im:
                cause = self.cp0[CP0_CAUSE] & ~(0x3F << 10)
                self.cp0[CP0_CAUSE] = cause | (ip << 10)
                self.cp0[CP0_STATUS] |= 2
                self.cp0[CP0_EPC] = self.pc
                use_bev = bool(self.cp0[CP0_STATUS] & 0x00400000)
                self.pc = 0x80000180 if not use_bev else 0xBFC00380
                self.next_pc = self.pc + 4
        word = self.core.bus.read_u32(self.pc)
        i = N64Opcode(word)
        self.execute(i)
        self.gpr[0] = 0
        self.cp0[CP0_COUNT] = u32(self.cp0[CP0_COUNT] + 1)

    def decode_name(self, o: N64Opcode) -> str:
        if o.op == 0:
            return SPECIAL_OPS.get(o.funct, "UNKNOWN")
        if o.op == 1:
            return REGIMM_OPS.get(o.rt, "UNKNOWN")
        if o.op == 0x10:
            if o.rs == 0x10:
                return COP0_CO.get(o.funct, "UNKNOWN")
            return COP0_RS.get(o.rs, "UNKNOWN")
        if o.op == 0x11:
            base = COP1_RS.get(o.rs, "UNKNOWN")
            if base in ("S", "D", "W", "L"):
                return f"{COP1_FUNCT.get(o.funct, 'UNKNOWN')}.{base}"
            return base
        return PRIMARY_OPS.get(o.op, "UNKNOWN")

    def _branch(self, target: int):
        self.next_pc = u32(target)

    def _skip_likely(self):
        self.pc = u32(self.pc + 4)
        self.next_pc = u32(self.pc + 4)

    def _write_tlb_entry(self, index: int):
        idx = index % 32
        hi = self.cp0[CP0_ENTRYHI]
        lo0 = self.cp0[CP0_ENTRYLO0]
        lo1 = self.cp0[CP0_ENTRYLO1]
        pagemask = self.cp0[CP0_PAGEMASK]
        self.tlb[idx].mask = pagemask
        self.tlb[idx].vpn2 = (hi >> 13) & 0x7FFFF
        self.tlb[idx].asid = hi & 0xFF
        self.tlb[idx].g = bool((lo0 & 1) and (lo1 & 1))
        self.tlb[idx].pfn0 = (lo0 >> 6) & 0xFFFFF
        self.tlb[idx].c0 = (lo0 >> 3) & 7
        self.tlb[idx].d0 = bool((lo0 >> 2) & 1)
        self.tlb[idx].v0 = bool((lo0 >> 1) & 1)
        self.tlb[idx].pfn1 = (lo1 >> 6) & 0xFFFFF
        self.tlb[idx].c1 = (lo1 >> 3) & 7
        self.tlb[idx].d1 = bool((lo1 >> 2) & 1)
        self.tlb[idx].v1 = bool((lo1 >> 1) & 1)

    def _test_cop1_usable(self) -> bool:
        return bool(self.cp0[CP0_STATUS] & 0x20000000)

    def _set_fp_exceptions(self):
        cause = (self.fcr31 >> 12) & 0x3F
        enable = ((self.fcr31 >> 7) & 0x1F) | 0x20
        if cause & enable:
            pass

    def _clear_fp_cause(self):
        self.fcr31 &= ~0x3F000

    def _set_fp_cause(self, cause: int):
        self.fcr31 &= ~0x3F000
        self.fcr31 |= (cause & 0x3F) << 12

    def _set_fp_flags(self, cause: int):
        self.fcr31 |= (cause & 0x3F) << 2

    def execute(self, o: N64Opcode):
        name = self.decode_name(o)
        old_pc = self.pc
        self.pc = self.next_pc
        self.next_pc = u32(self.next_pc + 4)
        g = self.gpr

        if name == "LUI":
            g[o.rt] = sx32_to_64(o.imm << 16)
        elif name == "ORI":
            g[o.rt] = u64(g[o.rs] | o.imm)
        elif name == "ANDI":
            g[o.rt] = u64(g[o.rs] & o.imm)
        elif name == "XORI":
            g[o.rt] = u64(g[o.rs] ^ o.imm)
        elif name == "ADDI":
            result = u32(g[o.rs] + o.simm)
            sign_rs = (g[o.rs] >> 31) & 1
            sign_imm = (o.simm >> 31) & 1
            sign_res = (result >> 31) & 1
            if sign_rs == sign_imm and sign_res != sign_rs:
                pass
            g[o.rt] = sx32_to_64(result)
        elif name == "ADDIU":
            g[o.rt] = sx32_to_64(u32(g[o.rs] + o.simm))
        elif name == "DADDI":
            g[o.rt] = u64(sign64(g[o.rs]) + o.simm)
        elif name == "DADDIU":
            g[o.rt] = u64(g[o.rs] + o.simm)
        elif name == "SLTI":
            g[o.rt] = 1 if sign64(g[o.rs]) < o.simm else 0
        elif name == "SLTIU":
            g[o.rt] = 1 if g[o.rs] < u64(o.simm) else 0

        elif name == "LB":
            g[o.rt] = sx8_to_64(self.core.bus.read_u8(g[o.rs] + o.simm))
        elif name == "LBU":
            g[o.rt] = self.core.bus.read_u8(g[o.rs] + o.simm)
        elif name == "LH":
            g[o.rt] = sx16_to_64(self.core.bus.read_u16(g[o.rs] + o.simm))
        elif name == "LHU":
            g[o.rt] = self.core.bus.read_u16(g[o.rs] + o.simm)
        elif name == "LW":
            g[o.rt] = sx32_to_64(self.core.bus.read_u32(g[o.rs] + o.simm))
        elif name == "LWU":
            g[o.rt] = self.core.bus.read_u32(g[o.rs] + o.simm)
        elif name == "LD":
            g[o.rt] = self.core.bus.read_u64(g[o.rs] + o.simm)
        elif name == "LWC1":
            addr = u32(g[o.rs] + o.simm)
            val = self.core.bus.read_u32(addr)
            self.fpr[o.rt] = u64((self.fpr[o.rt] & 0xFFFFFFFF00000000) | (val & MASK_32))
        elif name == "LDC1":
            addr = u32(g[o.rs] + o.simm)
            self.fpr[o.rt] = self.core.bus.read_u64(addr)

        elif name == "LWL":
            addr = u32(g[o.rs] + o.simm)
            offset = addr & 3
            align = addr & ~3
            val = self.core.bus.read_u32(align)
            g[o.rt] = sx32_to_64((u32(g[o.rt]) & _lwl_mask[offset]) | (val << _lwl_shift[offset]))

        elif name == "LWR":
            addr = u32(g[o.rs] + o.simm)
            offset = addr & 3
            align = addr & ~3
            val = self.core.bus.read_u32(align)
            g[o.rt] = sx32_to_64((u32(g[o.rt]) & _lwr_mask[offset]) | (val >> _lwr_shift[offset]))

        elif name == "LDL":
            addr = u32(g[o.rs] + o.simm)
            offset = addr & 7
            align = addr & ~7
            val = self.core.bus.read_u64(align)
            g[o.rt] = (g[o.rt] & _ldl_mask[offset]) | (val << _ldl_shift[offset])

        elif name == "LDR":
            addr = u32(g[o.rs] + o.simm)
            offset = addr & 7
            align = addr & ~7
            val = self.core.bus.read_u64(align)
            g[o.rt] = (g[o.rt] & _ldr_mask[offset]) | (val >> _ldr_shift[offset])

        elif name == "LL":
            addr = u32(g[o.rs] + o.simm)
            g[o.rt] = sx32_to_64(self.core.bus.read_u32(addr))
            self.llbit = True
            self.lladdr = addr & ~3
        elif name == "LLD":
            addr = u32(g[o.rs] + o.simm)
            g[o.rt] = self.core.bus.read_u64(addr)
            self.llbit = True
            self.lladdr = addr & ~7

        elif name == "SB":
            self.core.bus.write_u8(g[o.rs] + o.simm, u8(g[o.rt]))
        elif name == "SH":
            self.core.bus.write_u16(g[o.rs] + o.simm, u16(g[o.rt]))
        elif name == "SW":
            self.core.bus.write_u32(g[o.rs] + o.simm, u32(g[o.rt]))
        elif name == "SD":
            self.core.bus.write_u64(g[o.rs] + o.simm, g[o.rt])
        elif name == "SWC1":
            addr = u32(g[o.rs] + o.simm)
            self.core.bus.write_u32(addr, u32(self.fpr[o.rt]))
        elif name == "SDC1":
            addr = u32(g[o.rs] + o.simm)
            self.core.bus.write_u64(addr, self.fpr[o.rt])

        elif name == "SWL":
            addr = u32(g[o.rs] + o.simm)
            offset = addr & 3
            align = addr & ~3
            val = self.core.bus.read_u32(align)
            val = (val & _swl_mask[offset]) | (u32(g[o.rt]) >> _swl_shift[offset])
            self.core.bus.write_u32(align, val)

        elif name == "SWR":
            addr = u32(g[o.rs] + o.simm)
            offset = addr & 3
            align = addr & ~3
            val = self.core.bus.read_u32(align)
            val = (val & _swr_mask[offset]) | (u32(g[o.rt]) << _swr_shift[offset])
            self.core.bus.write_u32(align, val)

        elif name == "SDL":
            addr = u32(g[o.rs] + o.simm)
            offset = addr & 7
            align = addr & ~7
            val = self.core.bus.read_u64(align)
            val = (val & _sdl_mask[offset]) | (g[o.rt] >> _sdl_shift[offset])
            self.core.bus.write_u64(align, val)

        elif name == "SDR":
            addr = u32(g[o.rs] + o.simm)
            offset = addr & 7
            align = addr & ~7
            val = self.core.bus.read_u64(align)
            val = (val & _sdr_mask[offset]) | (g[o.rt] << _sdr_shift[offset])
            self.core.bus.write_u64(align, val)

        elif name == "SC":
            addr = u32(g[o.rs] + o.simm)
            if self.llbit and (addr & ~3) == self.lladdr:
                self.core.bus.write_u32(addr, u32(g[o.rt]))
                g[o.rt] = 1
            else:
                g[o.rt] = 0
            self.llbit = False
        elif name == "SCD":
            addr = u32(g[o.rs] + o.simm)
            if self.llbit and (addr & ~7) == self.lladdr:
                self.core.bus.write_u64(addr, g[o.rt])
                g[o.rt] = 1
            else:
                g[o.rt] = 0
            self.llbit = False

        elif name == "ADD":
            result = u32(g[o.rs] + g[o.rt])
            g[o.rd] = sx32_to_64(result)
        elif name == "ADDU":
            g[o.rd] = sx32_to_64(u32(g[o.rs] + g[o.rt]))
        elif name == "SUB":
            g[o.rd] = sx32_to_64(u32(g[o.rs] - g[o.rt]))
        elif name == "SUBU":
            g[o.rd] = sx32_to_64(u32(g[o.rs] - g[o.rt]))
        elif name == "DADD":
            g[o.rd] = u64(sign64(g[o.rs]) + sign64(g[o.rt]))
        elif name == "DADDU":
            g[o.rd] = u64(g[o.rs] + g[o.rt])
        elif name == "DSUB":
            g[o.rd] = u64(sign64(g[o.rs]) - sign64(g[o.rt]))
        elif name == "DSUBU":
            g[o.rd] = u64(g[o.rs] - g[o.rt])
        elif name == "AND":
            g[o.rd] = u64(g[o.rs] & g[o.rt])
        elif name == "OR":
            g[o.rd] = u64(g[o.rs] | g[o.rt])
        elif name == "XOR":
            g[o.rd] = u64(g[o.rs] ^ g[o.rt])
        elif name == "NOR":
            g[o.rd] = u64(~(g[o.rs] | g[o.rt]))
        elif name == "MOVZ":
            if g[o.rt] == 0:
                g[o.rd] = g[o.rs]
        elif name == "MOVN":
            if g[o.rt] != 0:
                g[o.rd] = g[o.rs]
        elif name == "SLT":
            g[o.rd] = 1 if sign64(g[o.rs]) < sign64(g[o.rt]) else 0
        elif name == "SLTU":
            g[o.rd] = 1 if g[o.rs] < g[o.rt] else 0

        elif name == "SLL":
            g[o.rd] = sx32_to_64(u32(g[o.rt]) << o.sa)
        elif name == "SRL":
            g[o.rd] = sx32_to_64(u32(g[o.rt]) >> o.sa)
        elif name == "SRA":
            g[o.rd] = sx32_to_64(sign32(g[o.rt]) >> o.sa)
        elif name == "SLLV":
            g[o.rd] = sx32_to_64(u32(g[o.rt]) << (g[o.rs] & 0x1F))
        elif name == "SRLV":
            g[o.rd] = sx32_to_64(u32(g[o.rt]) >> (g[o.rs] & 0x1F))
        elif name == "SRAV":
            g[o.rd] = sx32_to_64(sign32(g[o.rt]) >> (g[o.rs] & 0x1F))
        elif name == "DSLL":
            g[o.rd] = u64(g[o.rt] << o.sa)
        elif name == "DSRL":
            g[o.rd] = u64(g[o.rt] >> o.sa)
        elif name == "DSRA":
            g[o.rd] = u64(sign64(g[o.rt]) >> o.sa)
        elif name == "DSLLV":
            g[o.rd] = u64(g[o.rt] << (g[o.rs] & 0x3F))
        elif name == "DSRLV":
            g[o.rd] = u64(g[o.rt] >> (g[o.rs] & 0x3F))
        elif name == "DSRAV":
            g[o.rd] = u64(sign64(g[o.rt]) >> (g[o.rs] & 0x3F))
        elif name == "DSLL32":
            g[o.rd] = u64(g[o.rt] << (o.sa + 32))
        elif name == "DSRL32":
            g[o.rd] = u64(g[o.rt] >> (o.sa + 32))
        elif name == "DSRA32":
            g[o.rd] = u64(sign64(g[o.rt]) >> (o.sa + 32))

        elif name == "MFHI":
            g[o.rd] = self.hi
        elif name == "MTHI":
            self.hi = u64(g[o.rs])
        elif name == "MFLO":
            g[o.rd] = self.lo
        elif name == "MTLO":
            self.lo = u64(g[o.rs])
        elif name == "MULT":
            prod = sign32(g[o.rs]) * sign32(g[o.rt])
            self.lo = sx32_to_64(prod & MASK_32)
            self.hi = sx32_to_64((prod >> 32) & MASK_32)
        elif name == "MULTU":
            prod = u32(g[o.rs]) * u32(g[o.rt])
            self.lo = sx32_to_64(prod & MASK_32)
            self.hi = sx32_to_64((prod >> 32) & MASK_32)
        elif name == "DMULT":
            prod = sign64(g[o.rs]) * sign64(g[o.rt])
            self.lo = u64(prod)
            self.hi = u64(prod >> 64)
        elif name == "DMULTU":
            prod = g[o.rs] * g[o.rt]
            self.lo = u64(prod)
            self.hi = u64(prod >> 64)
        elif name == "DIV":
            a_s = sign32(g[o.rs])
            b_s = sign32(g[o.rt])
            b = u32(g[o.rt])
            if b != 0:
                if a_s == -0x80000000 and b_s == -1:
                    self.lo = sx32_to_64(-0x80000000)
                    self.hi = 0
                else:
                    self.lo = sx32_to_64(a_s // b_s)
                    self.hi = sx32_to_64(a_s % b_s)
            else:
                self.lo = 1 if a_s < 0 else -1
                self.hi = sx32_to_64(a_s)
        elif name == "DIVU":
            a = u32(g[o.rs])
            b = u32(g[o.rt])
            if b != 0:
                self.lo = sx32_to_64(a // b)
                self.hi = sx32_to_64(a % b)
            else:
                self.lo = -1
                self.hi = sx32_to_64(a)
        elif name in ("DDIV", "DDIVU"):
            a = g[o.rs]
            b = g[o.rt]
            if b != 0:
                if name == "DDIV":
                    self.lo = u64(sign64(a) // sign64(b))
                    self.hi = u64(sign64(a) % sign64(b))
                else:
                    self.lo = u64(a // b)
                    self.hi = u64(a % b)
            else:
                self.lo = 1 if sign64(a) < 0 else -1
                self.hi = u64(a)

        elif name in ("SYNC", "CACHE", "LWC2", "SWC2", "LWC3", "SWC3", "LDC2", "SDC2"):
            pass

        elif name == "J":
            self._branch(o.target_addr(old_pc))
        elif name == "JAL":
            g[31] = u64(old_pc + 8)
            self._branch(o.target_addr(old_pc))
        elif name == "JR":
            self._branch(g[o.rs])
        elif name == "JALR":
            g[o.rd] = u64(old_pc + 8)
            self._branch(g[o.rs])
        elif name == "BEQ":
            if g[o.rs] == g[o.rt]:
                self._branch(o.branch_addr(old_pc))
        elif name == "BNE":
            if g[o.rs] != g[o.rt]:
                self._branch(o.branch_addr(old_pc))
        elif name == "BLEZ":
            if sign64(g[o.rs]) <= 0:
                self._branch(o.branch_addr(old_pc))
        elif name == "BGTZ":
            if sign64(g[o.rs]) > 0:
                self._branch(o.branch_addr(old_pc))
        elif name == "BEQL":
            if g[o.rs] == g[o.rt]:
                self._branch(o.branch_addr(old_pc))
            else:
                self._skip_likely()
        elif name == "BNEL":
            if g[o.rs] != g[o.rt]:
                self._branch(o.branch_addr(old_pc))
            else:
                self._skip_likely()
        elif name == "BLEZL":
            if sign64(g[o.rs]) <= 0:
                self._branch(o.branch_addr(old_pc))
            else:
                self._skip_likely()
        elif name == "BGTZL":
            if sign64(g[o.rs]) > 0:
                self._branch(o.branch_addr(old_pc))
            else:
                self._skip_likely()
        elif name == "BLTZ":
            if sign64(g[o.rs]) < 0:
                self._branch(o.branch_addr(old_pc))
        elif name == "BGEZ":
            if sign64(g[o.rs]) >= 0:
                self._branch(o.branch_addr(old_pc))
        elif name == "BLTZL":
            if sign64(g[o.rs]) < 0:
                self._branch(o.branch_addr(old_pc))
            else:
                self._skip_likely()
        elif name == "BGEZL":
            if sign64(g[o.rs]) >= 0:
                self._branch(o.branch_addr(old_pc))
            else:
                self._skip_likely()
        elif name == "BC0":
            tf = o.rt & 1
            likely = bool(o.rt & 2)
            cond = False
            if cond == bool(tf):
                self._branch(o.branch_addr(old_pc))
            elif likely:
                self._skip_likely()
        elif name == "BLTZAL":
            g[31] = u64(old_pc + 8)
            if sign64(g[o.rs]) >= 0:
                self._branch(o.branch_addr(old_pc))
        elif name == "BLTZALL":
            g[31] = u64(old_pc + 8)
            if sign64(g[o.rs]) < 0:
                self._branch(o.branch_addr(old_pc))
            else:
                self._skip_likely()
        elif name == "BGEZALL":
            g[31] = u64(old_pc + 8)
            if sign64(g[o.rs]) >= 0:
                self._branch(o.branch_addr(old_pc))
            else:
                self._skip_likely()

        elif name == "MFC0":
            g[o.rt] = sx32_to_64(self.cp0[o.rd])
        elif name == "DMFC0":
            g[o.rt] = u64(self.cp0[o.rd])
        elif name == "CFC0":
            g[o.rt] = sx32_to_64(self.cp0[o.rd])
        elif name == "CTC0":
            self.cp0[o.rd] = u32(g[o.rt])
        elif name == "MTC0":
            if o.rd == CP0_INDEX:
                self.cp0[o.rd] = u32(g[o.rt]) & 0x8000003F
            elif o.rd == CP0_ENTRYLO0 or o.rd == CP0_ENTRYLO1:
                self.cp0[o.rd] = u32(g[o.rt]) & 0x3FFFFFFF
            elif o.rd == CP0_PAGEMASK:
                self.cp0[o.rd] = u32(g[o.rt]) & 0x01FFE000
            elif o.rd == CP0_WIRED:
                self.cp0[o.rd] = u32(g[o.rt]) & 0x3F
            elif o.rd == CP0_CONTEXT:
                self.cp0[o.rd] = (self.cp0[o.rd] & 0x7FFFFF) | (u32(g[o.rt]) & 0xFF800000)
            elif o.rd == CP0_COUNT:
                self.cp0[o.rd] = u32(g[o.rt])
            elif o.rd == CP0_COMPARE:
                self.cp0[o.rd] = u32(g[o.rt])
                self.cp0[CP0_CAUSE] &= ~(1 << 15)
            elif o.rd == CP0_ENTRYHI:
                self.cp0[o.rd] = u32(g[o.rt])
            elif o.rd == CP0_STATUS:
                self.cp0[o.rd] = u32(g[o.rt])
            elif o.rd in (CP0_LLADDR, CP0_CAUSE):
                self.cp0[o.rd] = u32(g[o.rt])
        elif name == "DMTC0":
            self.cp0[o.rd] = u64(g[o.rt])
        elif name == "ERET":
            target = self.cp0[CP0_ERROREPC] if (self.cp0[CP0_STATUS] & 0x4) else self.cp0[CP0_EPC]
            self.pc = u32(target)
            self.next_pc = u32(self.pc + 4)
            self.cp0[CP0_STATUS] &= ~0x6
        elif name == "TLBWI":
            idx = self.cp0[CP0_INDEX] & 0x1F
            self._write_tlb_entry(idx)
        elif name == "TLBWR":
            w = self.cp0[CP0_WIRED] & 0x1F
            idx = random.randint(w, 31)
            self._write_tlb_entry(idx)
        elif name == "TLBP":
            hi = self.cp0[CP0_ENTRYHI]
            vpn2 = (hi >> 13) & 0x7FFFF
            asid = hi & 0xFF
            match = -1
            for i, entry in enumerate(self.tlb):
                if entry.vpn2 == vpn2 and (entry.g or entry.asid == asid):
                    match = i
                    break
            self.cp0[CP0_INDEX] = match if match >= 0 else 0x80000000
        elif name == "TLBR":
            idx = self.cp0[CP0_INDEX] & 0x1F
            entry = self.tlb[idx]
            self.cp0[CP0_PAGEMASK] = entry.mask
            self.cp0[CP0_ENTRYHI] = (entry.vpn2 << 13) | entry.asid
            self.cp0[CP0_ENTRYLO0] = (entry.pfn0 << 6) | (entry.c0 << 3) | (entry.d0 << 2) | (entry.v0 << 1) | entry.g
            self.cp0[CP0_ENTRYLO1] = (entry.pfn1 << 6) | (entry.c1 << 3) | (entry.d1 << 2) | (entry.v1 << 1) | entry.g

        elif name in ("COP2", "COP3"):
            pass

        elif name in ("SYSCALL", "BREAK"):
            code = 8 if name == "SYSCALL" else 9
            self.cp0[CP0_CAUSE] = (self.cp0[CP0_CAUSE] & ~0x80000000) | (code << 2)
            self.cp0[CP0_EPC] = old_pc
            self.cp0[CP0_STATUS] |= 0x2
            use_bev = bool(self.cp0[CP0_STATUS] & 0x00400000)
            self.pc = 0x80000180 if not use_bev else 0xBFC00380
            self.next_pc = self.pc + 4

        elif name in ("TGE", "TGEU", "TLT", "TLTU", "TEQ", "TNE",
                       "TGEI", "TGEIU", "TLTI", "TLTIU", "TEQI", "TNEI"):
            pass

        elif name == "MFC1":
            if self._test_cop1_usable():
                g[o.rt] = sx32_to_64(self.fpr[o.rd] & MASK_32)
        elif name == "DMFC1":
            if self._test_cop1_usable():
                g[o.rt] = self.fpr[o.rd]
        elif name == "CFC1":
            if self._test_cop1_usable():
                g[o.rt] = sx32_to_64(self.fcr31 if o.rd == 31 else self.fcr0)
        elif name == "MTC1":
            if self._test_cop1_usable():
                self.fpr[o.rd] = u64((self.fpr[o.rd] & 0xFFFFFFFF00000000) | (g[o.rt] & MASK_32))
        elif name == "DMTC1":
            if self._test_cop1_usable():
                self.fpr[o.rd] = g[o.rt]
        elif name == "CTC1":
            if self._test_cop1_usable():
                if o.rd == 31:
                    self.fcr31 = u32(g[o.rt])
                elif o.rd == 0:
                    self.fcr0 = u32(g[o.rt])
        elif name == "BC1":
            if self._test_cop1_usable():
                tf = o.rt & 1
                likely = bool(o.rt & 2)
                cond = bool((self.fcr31 >> FCR31_COND_BIT) & 1)
                if cond == bool(tf):
                    self._branch(o.branch_addr(old_pc))
                elif likely:
                    self._skip_likely()
        elif "." in name:
            if not self._test_cop1_usable():
                pass
            self._clear_fp_cause()
            fmt = name.split(".")[-1]
            opname = name.split(".")[0]
            fs = o.rd
            fd = o.sa
            ft = o.rt
            if fmt in ("S", "W"):
                if opname == "CVT.S":
                    self.fpr[fd] = u64((self.fpr[fd] & 0xFFFFFFFF00000000) | (self.fpr[fs] & MASK_32))
                elif opname == "CVT.D":
                    f = bits_to_f32(self.fpr[fs] & MASK_32)
                    self.fpr[fd] = f64_to_bits(f)
                elif opname == "CVT.W":
                    f = bits_to_f32(self.fpr[fs] & MASK_32)
                    w = int(f) if not math.isnan(f) and not math.isinf(f) else 0
                    self.fpr[fd] = u64((self.fpr[fd] & 0xFFFFFFFF00000000) | (u32(w)))
                elif opname == "CVT.L":
                    f = bits_to_f32(self.fpr[fs] & MASK_32)
                    w = int(f) if not math.isnan(f) and not math.isinf(f) else 0
                    self.fpr[fd] = u64(w)
                elif opname == "ADD":
                    a = bits_to_f32(self.fpr[fs] & MASK_32)
                    b = bits_to_f32(self.fpr[ft] & MASK_32)
                    self.fpr[fd] = u64((self.fpr[fd] & 0xFFFFFFFF00000000) | (f32_to_bits(a + b)))
                elif opname == "SUB":
                    a = bits_to_f32(self.fpr[fs] & MASK_32)
                    b = bits_to_f32(self.fpr[ft] & MASK_32)
                    self.fpr[fd] = u64((self.fpr[fd] & 0xFFFFFFFF00000000) | (f32_to_bits(a - b)))
                elif opname == "MUL":
                    a = bits_to_f32(self.fpr[fs] & MASK_32)
                    b = bits_to_f32(self.fpr[ft] & MASK_32)
                    self.fpr[fd] = u64((self.fpr[fd] & 0xFFFFFFFF00000000) | (f32_to_bits(a * b)))
                elif opname == "DIV":
                    a = bits_to_f32(self.fpr[fs] & MASK_32)
                    b = bits_to_f32(self.fpr[ft] & MASK_32)
                    if b == 0.0:
                        self._set_fp_cause(FCR31_CAUSE_DIVBYZERO)
                        self._set_fp_flags(FCR31_CAUSE_DIVBYZERO)
                    self.fpr[fd] = u64((self.fpr[fd] & 0xFFFFFFFF00000000) | (f32_to_bits(a / b if b != 0 else float('inf'))))
                elif opname == "ABS":
                    f = bits_to_f32(self.fpr[fs] & MASK_32)
                    self.fpr[fd] = u64((self.fpr[fd] & 0xFFFFFFFF00000000) | (f32_to_bits(abs(f))))
                elif opname == "MOV":
                    self.fpr[fd] = u64((self.fpr[fd] & 0xFFFFFFFF00000000) | (self.fpr[fs] & MASK_32))
                elif opname == "NEG":
                    f = bits_to_f32(self.fpr[fs] & MASK_32)
                    self.fpr[fd] = u64((self.fpr[fd] & 0xFFFFFFFF00000000) | (f32_to_bits(-f)))
                elif opname == "SQRT":
                    f = bits_to_f32(self.fpr[fs] & MASK_32)
                    self.fpr[fd] = u64((self.fpr[fd] & 0xFFFFFFFF00000000) | (f32_to_bits(math.sqrt(f)) if f >= 0 else f32_to_bits(float('nan'))))
                elif opname in ("ROUND.W", "TRUNC.W", "CEIL.W", "FLOOR.W"):
                    f = bits_to_f32(self.fpr[fs] & MASK_32)
                    if math.isnan(f):
                        r = 0
                    elif math.isinf(f):
                        r = int(f)
                    else:
                        if opname == "ROUND.W":
                            r = int(round(f))
                        elif opname == "TRUNC.W":
                            r = int(f)
                        elif opname == "CEIL.W":
                            r = int(math.ceil(f))
                        else:
                            r = int(math.floor(f))
                    self.fpr[fd] = u64((self.fpr[fd] & 0xFFFFFFFF00000000) | (u32(r)))
                elif opname in ("ROUND.L", "TRUNC.L", "CEIL.L", "FLOOR.L"):
                    f = bits_to_f32(self.fpr[fs] & MASK_32)
                    if math.isnan(f) or math.isinf(f):
                        r = int(f) if math.isinf(f) else 0
                    else:
                        if opname == "ROUND.L":
                            r = int(round(f))
                        elif opname == "TRUNC.L":
                            r = int(f)
                        elif opname == "CEIL.L":
                            r = int(math.ceil(f))
                        else:
                            r = int(math.floor(f))
                    self.fpr[fd] = u64(r)
                elif opname in ("C.F", "C.UN", "C.EQ", "C.UEQ", "C.OLT", "C.ULT",
                                "C.OLE", "C.ULE", "C.SF", "C.NGLE", "C.SEQ",
                                "C.NGL", "C.LT", "C.NGE", "C.LE", "C.NGT"):
                    a = bits_to_f32(self.fpr[fs] & MASK_32)
                    b = bits_to_f32(self.fpr[ft] & MASK_32)
                    cond = False
                    if opname == "C.F":
                        cond = False
                    elif opname in ("C.UN", "C.UEQ", "C.ULE", "C.ULT", "C.NGLE", "C.NGL", "C.NGT", "C.NGE"):
                        cond = math.isnan(a) or math.isnan(b)
                    elif opname in ("C.EQ", "C.SEQ"):
                        cond = not math.isnan(a) and not math.isnan(b) and a == b
                    elif opname in ("C.OLT", "C.LT"):
                        cond = not math.isnan(a) and not math.isnan(b) and a < b
                    elif opname in ("C.ULT",):
                        cond = math.isnan(a) or math.isnan(b) or a < b
                    elif opname in ("C.OLE", "C.LE"):
                        cond = not math.isnan(a) and not math.isnan(b) and a <= b
                    elif opname in ("C.SF",):
                        cond = False
                    if cond:
                        self.fcr31 |= (1 << FCR31_COND_BIT)
                    else:
                        self.fcr31 &= ~(1 << FCR31_COND_BIT)
            elif fmt == "D":
                if opname == "ADD":
                    a = bits_to_f64(self.fpr[fs])
                    b = bits_to_f64(self.fpr[ft])
                    self.fpr[fd] = f64_to_bits(a + b)
                elif opname == "SUB":
                    a = bits_to_f64(self.fpr[fs])
                    b = bits_to_f64(self.fpr[ft])
                    self.fpr[fd] = f64_to_bits(a - b)
                elif opname == "MUL":
                    a = bits_to_f64(self.fpr[fs])
                    b = bits_to_f64(self.fpr[ft])
                    self.fpr[fd] = f64_to_bits(a * b)
                elif opname == "DIV":
                    a = bits_to_f64(self.fpr[fs])
                    b = bits_to_f64(self.fpr[ft])
                    if b == 0.0:
                        self._set_fp_cause(FCR31_CAUSE_DIVBYZERO)
                    self.fpr[fd] = f64_to_bits(a / b if b != 0 else float('inf'))
                elif opname == "ABS":
                    self.fpr[fd] = f64_to_bits(abs(bits_to_f64(self.fpr[fs])))
                elif opname == "MOV":
                    self.fpr[fd] = self.fpr[fs]
                elif opname == "NEG":
                    self.fpr[fd] = f64_to_bits(-bits_to_f64(self.fpr[fs]))
                elif opname == "SQRT":
                    f = bits_to_f64(self.fpr[fs])
                    self.fpr[fd] = f64_to_bits(math.sqrt(f) if f >= 0 else float('nan'))
                elif opname == "CVT.S":
                    f = bits_to_f64(self.fpr[fs])
                    self.fpr[fd] = u64((self.fpr[fd] & 0xFFFFFFFF00000000) | (f32_to_bits(float(f))))
                elif opname == "CVT.W":
                    f = bits_to_f64(self.fpr[fs])
                    w = int(f) if not math.isnan(f) and not math.isinf(f) else 0
                    self.fpr[fd] = u64((self.fpr[fd] & 0xFFFFFFFF00000000) | (u32(w)))
                elif opname == "CVT.L":
                    f = bits_to_f64(self.fpr[fs])
                    w = int(f) if not math.isnan(f) and not math.isinf(f) else 0
                    self.fpr[fd] = u64(w)
                elif opname in ("ROUND.W", "TRUNC.W", "CEIL.W", "FLOOR.W"):
                    f = bits_to_f64(self.fpr[fs])
                    if math.isnan(f) or math.isinf(f):
                        r = 0
                    else:
                        if opname == "ROUND.W": r = int(round(f))
                        elif opname == "TRUNC.W": r = int(f)
                        elif opname == "CEIL.W": r = int(math.ceil(f))
                        else: r = int(math.floor(f))
                    self.fpr[fd] = u64((self.fpr[fd] & 0xFFFFFFFF00000000) | (u32(r)))
                elif opname in ("ROUND.L", "TRUNC.L", "CEIL.L", "FLOOR.L"):
                    f = bits_to_f64(self.fpr[fs])
                    if math.isnan(f) or math.isinf(f):
                        r = 0
                    else:
                        if opname == "ROUND.L": r = int(round(f))
                        elif opname == "TRUNC.L": r = int(f)
                        elif opname == "CEIL.L": r = int(math.ceil(f))
                        else: r = int(math.floor(f))
                    self.fpr[fd] = u64(r)
                elif opname in ("C.F", "C.UN", "C.EQ", "C.UEQ", "C.OLT", "C.ULT",
                                "C.OLE", "C.ULE", "C.SF", "C.NGLE", "C.SEQ",
                                "C.NGL", "C.LT", "C.NGE", "C.LE", "C.NGT"):
                    a = bits_to_f64(self.fpr[fs])
                    b = bits_to_f64(self.fpr[ft])
                    cond = False
                    if opname == "C.F": cond = False
                    elif opname in ("C.UN", "C.UEQ", "C.ULE", "C.ULT", "C.NGLE", "C.NGL", "C.NGT", "C.NGE"):
                        cond = math.isnan(a) or math.isnan(b)
                    elif opname in ("C.EQ", "C.SEQ"):
                        cond = not math.isnan(a) and not math.isnan(b) and a == b
                    elif opname in ("C.OLT", "C.LT"):
                        cond = not math.isnan(a) and not math.isnan(b) and a < b
                    elif opname in ("C.OLE", "C.LE"):
                        cond = not math.isnan(a) and not math.isnan(b) and a <= b
                    elif opname == "C.SF": cond = False
                    if cond:
                        self.fcr31 |= (1 << FCR31_COND_BIT)
                    else:
                        self.fcr31 &= ~(1 << FCR31_COND_BIT)
            elif fmt in ("L",):
                if opname == "CVT.S" or opname == "CVT.D":
                    f = bits_to_f64(self.fpr[fs])
                    if opname == "CVT.S":
                        self.fpr[fd] = u64((self.fpr[fd] & 0xFFFFFFFF00000000) | (f32_to_bits(float(f))))
                    else:
                        self.fpr[fd] = f64_to_bits(float(f))
                elif opname == "CVT.W" or opname == "CVT.L":
                    f = bits_to_f64(self.fpr[fs])
                    if opname == "CVT.W":
                        self.fpr[fd] = u64((self.fpr[fd] & 0xFFFFFFFF00000000) | (f32_to_bits(float(f))))
                    else:
                        self.fpr[fd] = f64_to_bits(float(f))

class ACsN64Core:
    def __init__(self):
        self.rom = bytearray()
        self.rdram = bytearray(RDRAM_SIZE)
        self.rsp_dmem = bytearray(RSP_DMEM_SIZE)
        self.rsp_imem = bytearray(RSP_IMEM_SIZE)
        self.pif_ram = bytearray(PIF_RAM_SIZE)

        self.bus = DeviceBus(self)
        self.cpu = CPUCore(self)
        self.cathle_plugin_slots: Tuple[CathlePluginSlot, ...] = cathle_plugin_slots_monolith()
        self.n64_system = CathleSystemFacade(self)

        self.rom_name = "None"
        self.is_running = False
        self.has_booted = False
        self.frame_count = 0
        self.hle_calls = 0

        self.controller_state = 0x0000
        self.rdp_draw_commands = []
        self.audio_samples_played = 0
        self._update_framebuffer = False
        self._last_vi_origin = 0
        self._vi_origin_set = False

    def mirror_rom_to_rdram_bios(self) -> None:
        if len(self.rom) < 0x40:
            return
        linear_cap = min(len(self.rom), RDRAM_SIZE)
        if linear_cap > 0:
            self.rdram[0:linear_cap] = self.rom[0:linear_cap]
        rom_window = min(0x200000, len(self.rom), RDRAM_SIZE - 0x100000)
        if rom_window > 0:
            self.rdram[0x100000 : 0x100000 + rom_window] = self.rom[0:rom_window]

    def load_rom(self, path: str):
        with open(path, "rb") as f:
            data = f.read()
        self.rom = self.normalize_rom(bytearray(data))
        self.rom_name = os.path.basename(path)
        self.header = N64Header(self.rom)
        self.reset()
        self.has_booted = False
        self.mirror_rom_to_rdram_bios()

    def load_rom_data(self, data: bytearray, name: str = "game.n64"):
        self.rom = self.normalize_rom(bytearray(data))
        self.rom_name = name
        self.header = N64Header(self.rom)
        self.reset()
        self.has_booted = False
        self.mirror_rom_to_rdram_bios()

    def normalize_rom(self, data: bytearray) -> bytearray:
        strip_documentation_header_if_present(data)
        if len(data) < 4:
            return data
        magic = data[0:4]
        if magic == Z64_BIG_ENDIAN_MAGIC:
            apply_ultra64_cart_header_defaults(data)
            return data
        if magic == V64_MAGIC:
            for i in range(0, len(data) - 1, 2):
                data[i], data[i + 1] = data[i + 1], data[i]
            apply_ultra64_cart_header_defaults(data)
            return data
        if magic == N64_LE_MAGIC:
            for i in range(0, len(data) - 3, 4):
                data[i], data[i + 3] = data[i + 3], data[i]
                data[i + 1], data[i + 2] = data[i + 2], data[i + 1]
            apply_ultra64_cart_header_defaults(data)
            return data
        return data

    def boot(self) -> bool:
        if len(self.rom) < 0x1000:
            return False
        self.reset()
        self.header = N64Header(self.rom)
        seed_commercial_pif_ram(self.pif_ram)
        self.mirror_rom_to_rdram_bios()

        put_be32(self.rdram, 0x318, 0x00800000)

        entry = normalize_commercial_entry(self.header.boot_address)
        self.cpu.pc = entry
        self.cpu.next_pc = u32(entry + 4)
        self.cpu.gpr[29] = u64(0x803FA800)
        self.cpu.gpr[30] = u64(0x803FA800)
        self.cpu.cp0[CP0_STATUS] = 0x34000000
        self.cpu.cp0[CP0_CONFIG] = 0x0006E463
        self.bus.regs[0x04600010] = 0

        self.has_booted = True
        self.is_running = True
        return True

    def reset(self):
        self.rdram = bytearray(RDRAM_SIZE)
        self.rsp_dmem = bytearray(RSP_DMEM_SIZE)
        self.rsp_imem = bytearray(RSP_IMEM_SIZE)
        self.pif_ram = bytearray(PIF_RAM_SIZE)
        self.bus.reset()
        self.cpu.reset()
        self.frame_count = 0
        self.hle_calls = 0
        self.rdp_draw_commands.clear()
        self.audio_samples_played = 0
        self._vi_origin_set = False

    def trigger_pi_dma(self):
        dram_addr = self.bus.regs.get(0x04600000, 0) & 0x00FFFFFF
        cart_addr = self.bus.regs.get(0x04600004, 0) & 0x1FFFFFFF
        if cart_addr >= 0x10000000:
            cart_addr -= 0x10000000
        length = (self.bus.regs.get(0x0460000C, 0) & 0x00FFFFFF) + 1
        if cart_addr >= len(self.rom) or dram_addr >= RDRAM_SIZE:
            self.bus.hw_interrupts |= 0x10
            return
        actual_len = min(length, len(self.rom) - cart_addr, RDRAM_SIZE - dram_addr)
        if actual_len > 0:
            self.rdram[dram_addr:dram_addr + actual_len] = self.rom[cart_addr:cart_addr + actual_len]
        self.bus.hw_interrupts |= 0x10

    def trigger_sp_dma(self, to_rsp: bool):
        sp_addr = self.bus.regs.get(0x04040000, 0) & 0x1FFF
        dram_addr = self.bus.regs.get(0x04040004, 0) & 0x00FFFFFF
        reg = 0x04040008 if to_rsp else 0x0404000C
        length = (self.bus.regs.get(reg, 0) & 0xFFF) + 1
        target = self.rsp_imem if sp_addr & 0x1000 else self.rsp_dmem
        off = sp_addr & 0xFFF
        length = min(length, 0x1000 - off, max(0, RDRAM_SIZE - dram_addr))
        if length <= 0:
            return
        if to_rsp:
            target[off:off + length] = self.rdram[dram_addr:dram_addr + length]
        else:
            self.rdram[dram_addr:dram_addr + length] = target[off:off + length]
        self.bus.hw_interrupts |= 0x01

    def trigger_si_dma(self, read_pif: bool):
        dram_addr = self.bus.regs.get(0x04800000, 0) & 0x00FFFFFF
        xfer = min(64, max(0, RDRAM_SIZE - dram_addr))
        if xfer <= 0:
            self.bus.regs[0x04800018] = 0
            return
        if read_pif:
            seed_commercial_pif_ram(self.pif_ram)
            for ch in range(4):
                off = 0x20 + ch * 4
                btn = self.controller_state ^ 0xFFFF
                struct.pack_into(">HB", self.pif_ram, off, btn & 0xFFFF, 0x00)
            self.rdram[dram_addr:dram_addr + xfer] = self.pif_ram[0:xfer]
        else:
            self.pif_ram[0:xfer] = self.rdram[dram_addr:dram_addr + xfer]
            if xfer < 64:
                self.pif_ram[xfer:64] = bytearray(64 - xfer)
        self.bus.regs[0x04800018] = 0
        self.bus.hw_interrupts |= 0x02

    def process_rsp(self):
        self.hle_calls += 1
        self.bus.regs[0x04040010] |= 1
        self.bus.hw_interrupts |= 0x01

    def process_rdp(self):
        start_addr = self.bus.regs.get(0x04100000, 0) & 0x00FFFFFF
        end_addr = self.bus.regs.get(0x04100004, 0) & 0x00FFFFFF
        self.rdp_draw_commands.clear()
        if end_addr > start_addr:
            self.bus.regs[0x04100008] = end_addr
            self.bus.regs[0x0410000C] = 0
        self.bus.hw_interrupts |= 0x20

    def process_audio(self):
        length = self.bus.regs.get(0x04500004, 0)
        self.audio_samples_played += length
        self.bus.regs[0x0450000C] = 0
        self.bus.hw_interrupts |= 0x04

    def vi_framebuffer_phys_origin(self) -> int:
        reg = self.bus.regs.get(VI_ORIGIN_REG, 0) & 0xFFFFFFFF
        segment = (reg >> 29) & 7
        if segment in (4, 5):
            reg = reg & 0x1FFFFFFF
        elif segment == 6:
            reg = reg & 0x1FFFFFFF
        reg &= 0xFFFFFF
        return reg if reg != 0 else 0x00100000

    def vi_display_width_height(self) -> Tuple[int, int]:
        w = self.bus.regs.get(VI_WIDTH_REG, 320) & 0xFFF
        if w < 64 or w > 1024:
            w = 320
        return w, 240

    def vi_framebuffer_ppm(self) -> bytes | None:
        w, h = self.vi_display_width_height()
        ow, oh = min(320, w), min(240, h)
        phys = self.vi_framebuffer_phys_origin()
        origins_to_try = [
            phys,
            0x00100000,
            0x00200000,
            0x00000000,
            phys & 0xFFF00000,
            phys - 0x100000 if phys >= 0x100000 else phys,
            phys + 0x100000 if phys + 0x100000 < RDRAM_SIZE else phys,
        ]
        seen = set()
        for origin in origins_to_try:
            if origin in seen:
                continue
            seen.add(origin)
            p = rdram_rgb5551_to_ppm(self.rdram, origin, ow, oh)
            if p:
                return p
        return None

    def run_frame(self):
        steps = 50000000 if not self._vi_origin_set else 20000000
        for _ in range(steps):
            self.n64_system.step_cpu_instruction()
        self.frame_count += 1
        self.bus.regs[0x04400010] = 0 if self.frame_count & 1 else 0x200
        self.bus.hw_interrupts |= 0x08

def _format_rom_size(num_bytes: int) -> str:
    if num_bytes >= 1024 * 1024:
        return f"{num_bytes / (1024 * 1024):.1f} MB"
    if num_bytes >= 1024:
        return f"{num_bytes / 1024:.0f} KB"
    return f"{num_bytes} B"

def probe_rom_entry(path: str) -> Optional[Dict[str, str]]:
    try:
        with open(path, "rb") as f:
            data = bytearray(f.read(min(0x1000, 64 * 1024 * 1024)))
    except OSError:
        return None
    if len(data) < 0x40:
        return None
    data = normalize_rom_bytes(data)
    header = N64Header(data)
    fname = os.path.basename(path)
    internal = header.title or "UNKNOWN"
    good = internal if internal != "UNKNOWN" else os.path.splitext(fname)[0]
    return {
        "path": path,
        "file_name": fname,
        "internal_name": internal[:20],
        "good_name": good[:40],
        "status": "Unknown",
        "rom_size": _format_rom_size(os.path.getsize(path)),
    }

def scan_rom_directory(directory: str) -> List[Dict[str, str]]:
    entries: List[Dict[str, str]] = []
    if not directory or not os.path.isdir(directory):
        return entries
    found = 0
    for root, _dirs, files in os.walk(directory):
        for name in sorted(files):
            if found >= _ROM_SCAN_MAX_FILES:
                return entries
            low = name.lower()
            if not any(low.endswith(ext) for ext in ROM_EXTENSIONS):
                continue
            full = os.path.join(root, name)
            row = probe_rom_entry(full)
            if row:
                entries.append(row)
                found += 1
    return entries

class ACsN64GUI:
    def __init__(self) -> None:
        if tk is None:
            raise RuntimeError("Tkinter is not available. Install Python with tk support.")
        if ttk is None:
            raise RuntimeError("tkinter.ttk is not available.")
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            raise RuntimeError(f"Cannot open Tk display: {exc}") from exc

        self.root.title(WINDOW_TITLE)
        self.root.geometry("920x560")
        self.root.minsize(720, 420)
        self.root.configure(bg=CATHLE_WIN_GRAY)

        self.core = ACsN64Core()
        self._fb_photo = None
        self._ppm_stream = None
        self.rom_dir = default_rom_directory()
        self.rom_entries: List[Dict[str, str]] = []
        self._selected_rom_path: Optional[str] = None
        self.game_window: Optional[tk.Toplevel] = None
        self.canvas: Optional[tk.Canvas] = None
        self.monitor: Optional[tk.Text] = None

        self._setup_ui()
        self.info_text.set(f"Ready — scanning {_DEFAULT_ROM_DIR if self.rom_dir == _DEFAULT_ROM_DIR else self.rom_dir}...")
        self.root.update_idletasks()
        self.root.after(50, self._deferred_rom_scan)
        self._set_file_menu_running_state(False)
        self._update_loop()

    def _deferred_rom_scan(self) -> None:
        try:
            self.refresh_rom_list()
        except Exception as exc:
            self.info_text.set(f"ROM scan error: {exc}")

    def _cathle_button(self, parent: tk.Misc, text: str, command, width: int = 10) -> tk.Button:
        return tk.Button(
            parent, text=text, command=command, width=width, font=UI_FONT,
            bg=CATHLE_BTN_FACE, activebackground=CATHLE_BTN_HIGHLIGHT,
            relief=tk.RAISED, bd=2, highlightthickness=0,
        )

    def _setup_ui(self) -> None:
        menubar = tk.Menu(self.root, tearoff=0, bg=CATHLE_WIN_FACE, fg=CATHLE_TEXT)

        self.file_menu = tk.Menu(menubar, tearoff=0)
        self.file_menu.add_command(label="Open Rom", command=self.open_rom, accelerator="Ctrl+O")
        self.file_menu.add_command(label="Rom Information", command=self.show_rom_info, accelerator="Ctrl+I")
        self.file_menu.add_command(label="Game Information", state="disabled")
        self.file_menu.add_separator()
        self.file_menu.add_command(
            label="Start Emulation", command=self.start_emulation, accelerator="F10", state="disabled"
        )
        self.file_menu.add_separator()
        self.file_menu.add_command(label="Choose Rom Directory...", command=self.choose_rom_directory)
        self.file_menu.add_command(label="Refresh Rom List", command=self.refresh_rom_list, accelerator="F5")
        self.file_menu.add_separator()
        self.file_menu.add_command(label="Exit", command=self.root.quit, accelerator="Alt+F4")
        menubar.add_cascade(label="File", menu=self.file_menu)

        system_menu = tk.Menu(menubar, tearoff=0)
        system_menu.add_command(label="Reset", command=self.reset_emu, accelerator="F1")
        system_menu.add_separator()
        system_menu.add_command(label="Limit FPS", state="disabled")
        menubar.add_cascade(label="System", menu=system_menu)

        options_menu = tk.Menu(menubar, tearoff=0)
        options_menu.add_command(label="Settings...", state="disabled")
        menubar.add_cascade(label="Options", menu=options_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="About cathle...", command=self.show_about)
        menubar.add_cascade(label="Help", menu=help_menu)
        self.root.config(menu=menubar)

        toolbar = tk.Frame(self.root, bg=CATHLE_WIN_GRAY, relief=tk.RAISED, bd=1)
        toolbar.pack(side=tk.TOP, fill=tk.X)
        for label, cmd in (
            ("Open Rom", self.open_rom),
            ("Start", self.start_emulation),
            ("Reset", self.reset_emu),
            ("Refresh", self.refresh_rom_list),
        ):
            self._cathle_button(toolbar, label, cmd, width=8).pack(side=tk.LEFT, padx=2, pady=2)

        tk.Label(
            toolbar,
            text=f"{ENGINE_NAME} | legacy files=ON",
            bg=CATHLE_WIN_GRAY, fg=CATHLE_TEXT, font=UI_FONT,
        ).pack(side=tk.RIGHT, padx=8)

        browser_frame = tk.Frame(self.root, bg=CATHLE_VIEWPORT_BORDER, relief=tk.SUNKEN, bd=2)
        browser_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        inner = tk.Frame(browser_frame, bg=CATHLE_PANEL_WHITE)
        inner.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        cols = [c[0] for c in ROM_BROWSER_COLUMNS]
        self.rom_tree = ttk.Treeview(inner, columns=cols, show="headings", selectmode="browse")
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Treeview",
            background=CATHLE_PANEL_WHITE,
            fieldbackground=CATHLE_PANEL_WHITE,
            foreground=CATHLE_TEXT,
            rowheight=18,
            font=UI_FONT,
        )
        style.configure("Treeview.Heading", font=UI_FONT_BOLD, background=CATHLE_WIN_GRAY)
        style.map(
            "Treeview",
            background=[("selected", CATHLE_LIST_SEL_BG)],
            foreground=[("selected", CATHLE_LIST_SEL_FG)],
        )
        for key, heading, width in ROM_BROWSER_COLUMNS:
            self.rom_tree.heading(key, text=heading, anchor=tk.W)
            self.rom_tree.column(key, width=width, minwidth=60, anchor=tk.W)

        scroll = ttk.Scrollbar(inner, orient=tk.VERTICAL, command=self.rom_tree.yview)
        self.rom_tree.configure(yscrollcommand=scroll.set)
        self.rom_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.rom_tree.bind("<Double-Button-1>", lambda _e: self._load_selected_rom())
        self.rom_tree.bind("<<TreeviewSelect>>", self._on_rom_select)

        self.info_text = tk.StringVar(value=f"Ready — {APP_NAME} ROM browser")
        self.status_bar = tk.Label(
            self.root, textvariable=self.info_text, bd=1, relief=tk.SUNKEN,
            anchor=tk.W, bg=CATHLE_WIN_FACE, fg=CATHLE_TEXT, font=UI_FONT, padx=4, pady=1,
        )
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)

        self.root.bind_all("<Control-o>", lambda _e: self.open_rom())
        self.root.bind_all("<Control-O>", lambda _e: self.open_rom())
        self.root.bind_all("<Control-i>", lambda _e: self.show_rom_info())
        self.root.bind_all("<Control-I>", lambda _e: self.show_rom_info())
        self.root.bind_all("<F5>", lambda _e: self.refresh_rom_list())
        self.root.bind_all("<F10>", lambda _e: self.start_emulation())
        self.root.bind_all("<F11>", lambda _e: self.stop_emulation())
        self.root.bind_all("<F1>", lambda _e: self.reset_emu())

    def _on_rom_select(self, _event=None) -> None:
        sel = self.rom_tree.selection()
        if not sel:
            self._selected_rom_path = None
            return
        iid = sel[0]
        idx = self.rom_tree.index(iid)
        if 0 <= idx < len(self.rom_entries):
            self._selected_rom_path = self.rom_entries[idx]["path"]
            self.info_text.set(self._selected_rom_path)

    def _load_selected_rom(self) -> None:
        if self._selected_rom_path:
            self.core.load_rom(self._selected_rom_path)
            self._set_file_menu_running_state(False)
            self.info_text.set(f"Loaded: {self.core.rom_name}")

    def _set_file_menu_running_state(self, running: bool) -> None:
        has_rom = bool(self.core.rom)
        try:
            self.file_menu.entryconfig(4, state="normal" if has_rom and not running else "disabled")
            self.file_menu.entryconfig(5, state="normal" if has_rom else "disabled")
        except tk.TclError:
            pass

    def refresh_rom_list(self) -> None:
        self.rom_tree.delete(*self.rom_tree.get_children())
        self.rom_entries = scan_rom_directory(self.rom_dir)
        for row in self.rom_entries:
            self.rom_tree.insert(
                "", tk.END,
                values=(
                    row["file_name"],
                    row["internal_name"],
                    row["good_name"],
                    row["status"],
                    row["rom_size"],
                ),
            )
        self.info_text.set(f"{len(self.rom_entries)} ROM(s) in {self.rom_dir}")

    def choose_rom_directory(self) -> None:
        path = filedialog.askdirectory(title="Choose Rom Directory", initialdir=self.rom_dir)
        if path:
            self.rom_dir = path
            self.refresh_rom_list()

    def _set_monitor_text(self, text: str) -> None:
        if self.monitor is None:
            return
        self.monitor.configure(state="normal")
        self.monitor.delete("1.0", tk.END)
        self.monitor.insert("1.0", text)
        self.monitor.configure(state="disabled")

    def _set_monitor_idle(self) -> None:
        rom = self.core.rom_name or "None"
        booted = "Yes" if self.core.has_booted else "No"
        run = "Running" if self.core.is_running else "Stopped"
        self._set_monitor_text(
            f"{APP_NAME}  [{ENGINE_NAME}]\n"
            f"legacy files: ON\n"
            f"{'=' * 40}\n\n"
            f"Status : {run}\n"
            f"Booted : {booted}\n"
            f"ROM    : {rom}\n\n"
            f"F10 Start | F1 Reset\n"
        )

    def _draw_splash(self) -> None:
        if self.canvas is None:
            return
        self.canvas.delete("splash")
        self.canvas.create_text(
            160, 88, text=APP_NAME, fill=CATHLE_SPLASH_GRAY,
            font=UI_FONT_BOLD, justify=tk.CENTER, tags="splash",
        )
        self.canvas.create_text(
            160, 128, text="cathle HLE preview\n\nEnd emulation to return",
            fill=CATHLE_SPLASH_GRAY, font=UI_FONT, justify=tk.CENTER, tags="splash",
        )

    def _open_game_window(self) -> None:
        if self.game_window is not None:
            return
        self.game_window = tk.Toplevel(self.root)
        self.game_window.title(f"{WINDOW_TITLE} — {self.core.rom_name}")
        self.game_window.configure(bg=CATHLE_WIN_GRAY)
        self.game_window.protocol("WM_DELETE_WINDOW", self.stop_emulation)

        body = tk.Frame(self.game_window, bg=CATHLE_WIN_GRAY)
        body.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        left = tk.Frame(body, bg=CATHLE_WIN_GRAY)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        tk.Label(left, text="CPU / status", bg=CATHLE_WIN_GRAY, fg=CATHLE_TEXT, font=UI_FONT_BOLD).pack(anchor=tk.W)
        self.monitor = tk.Text(
            left, width=36, height=18, wrap=tk.WORD, font=UI_FONT_MONO,
            bg=CATHLE_PANEL_WHITE, fg=CATHLE_TEXT, relief=tk.SUNKEN, bd=2,
            highlightthickness=0, state="disabled", padx=6, pady=6,
        )
        self.monitor.pack(fill=tk.BOTH, expand=True)
        self._set_monitor_idle()

        right = tk.Frame(body, bg=CATHLE_WIN_GRAY)
        right.pack(side=tk.LEFT, fill=tk.Y, padx=(6, 0))
        tk.Label(right, text="Graphics", bg=CATHLE_WIN_GRAY, fg=CATHLE_TEXT, font=UI_FONT_BOLD).pack(anchor=tk.W)
        viewport = tk.Frame(right, bg=CATHLE_VIEWPORT_BORDER, relief=tk.SUNKEN, bd=2)
        viewport.pack()
        inner = tk.Frame(viewport, bg="black")
        inner.pack(padx=2, pady=2)
        self.canvas = tk.Canvas(inner, width=320, height=240, bg="black", highlightthickness=0, bd=0)
        self.canvas.pack()
        self._draw_splash()

        self.game_window.bind("<KeyPress>", self._on_key_press)
        self.game_window.bind("<KeyRelease>", self._on_key_release)
        self._refresh_vi_framebuffer()

    def _close_game_window(self) -> None:
        if self.game_window is not None:
            self.game_window.destroy()
            self.game_window = None
        self.canvas = None
        self.monitor = None
        self._fb_photo = None

    def _bind_controls(self) -> None:
        self.key_map = {
            "Up": 0x0800, "Down": 0x0400, "Left": 0x0200, "Right": 0x0100,
            "Return": 0x1000, "z": 0x8000, "x": 0x4000,
        }

    def _on_key_press(self, event: tk.Event) -> None:
        if event.keysym in self.key_map:
            self.core.controller_state |= self.key_map[event.keysym]

    def _on_key_release(self, event: tk.Event) -> None:
        if event.keysym in self.key_map:
            self.core.controller_state &= ~self.key_map[event.keysym]

    def show_rom_info(self) -> None:
        if not self.core.rom:
            if messagebox:
                messagebox.showinfo("Rom Information", "No ROM loaded.", parent=self.root)
            return
        h = self.core.header
        if messagebox:
            messagebox.showinfo(
                "Rom Information",
                f"File: {self.core.rom_name}\n"
                f"Internal Name: {h.title}\n"
                f"Cart ID: {h.cart_id}\n"
                f"CRC1: 0x{h.crc1:08X}\n"
                f"CRC2: 0x{h.crc2:08X}\n"
                f"Entry: 0x{h.boot_address:08X}\n"
                f"Size: {_format_rom_size(len(self.core.rom))}",
                parent=self.root,
            )

    def show_about(self) -> None:
        if messagebox:
            messagebox.showinfo(
                f"About {APP_NAME}",
                f"{APP_NAME}\n\n"
                f"Engine: {ENGINE_NAME} (in-file Python)\n"
                f"Engine files: ON\n"
                f"PJ64-Legacy v1.6.4 interpreter algorithms\n\n"
                f"Python {PYTHON_TARGET} — clean-room port.\n",
                parent=self.root,
            )

    def close_rom(self) -> None:
        self.stop_emulation()
        self.core.rom = bytearray()
        self.core.rom_name = "None"
        self.core.has_booted = False
        self.core.reset()
        self._set_file_menu_running_state(False)
        self.info_text.set(f"Ready — {len(self.rom_entries)} ROM(s)")

    def open_rom(self) -> None:
        path = filedialog.askopenfilename(
            title="Open Rom",
            filetypes=[("N64 ROMs", "*.z64 *.v64 *.n64 *.rom *.bin"), ("All files", "*.*")],
        )
        if not path:
            return
        self.core.load_rom(path)
        self._selected_rom_path = path
        row = probe_rom_entry(path)
        if row and not any(e["path"] == path for e in self.rom_entries):
            self.rom_entries.append(row)
            self.rom_tree.insert(
                "", tk.END,
                values=(
                    row["file_name"], row["internal_name"], row["good_name"],
                    row["status"], row["rom_size"],
                ),
            )
        self._set_file_menu_running_state(False)
        self.info_text.set(f"Loaded: {self.core.rom_name}")

    def start_emulation(self) -> None:
        if not self.core.rom:
            if self._selected_rom_path:
                self.core.load_rom(self._selected_rom_path)
            else:
                self.info_text.set("No ROM loaded — select a ROM or use Open Rom")
                return
        if not self.core.has_booted:
            if not self.core.boot():
                self.info_text.set("Boot failed — invalid ROM?")
                self._open_game_window()
                self._canvas_static_fallback("Boot failed (ROM < 4 KiB or reset error)")
                return
        self._open_game_window()
        self._bind_controls()
        self.core.is_running = True
        self._set_file_menu_running_state(True)
        name = (
            self.core.header.title
            if getattr(self.core, "header", None) and self.core.header.title
            else self.core.rom_name
        )
        self.info_text.set(f"Emulating: {name}")
        if self.game_window:
            self.game_window.title(f"{WINDOW_TITLE} — {name}")

    def stop_emulation(self) -> None:
        self.core.is_running = False
        self._close_game_window()
        self._set_file_menu_running_state(False)
        self.info_text.set(f"Stopped — {len(self.rom_entries)} ROM(s) in browser")

    def toggle_run(self):
        if self.core.is_running:
            self.stop_emulation()
        else:
            self.start_emulation()

    def reset_emu(self) -> None:
        was = self.core.rom_name
        running = self.core.is_running
        self.core.reset()
        self.core.has_booted = False
        self.core.is_running = False
        if self.core.rom:
            self.core.rom_name = was
            self.core.mirror_rom_to_rdram_bios()
        if self.canvas is not None:
            self.canvas.delete("all")
            self._draw_splash()
        self._fb_photo = None
        self._set_monitor_idle()
        self._set_file_menu_running_state(False)
        if running:
            self._open_game_window()
            self.core.is_running = True
            self._set_file_menu_running_state(True)
        if self.core.rom and self.canvas is not None:
            self._refresh_vi_framebuffer()

    def _canvas_static_fallback(self, subtitle: str | None = None) -> None:
        if self.canvas is None:
            return
        self.canvas.delete("fb")
        self.canvas.delete("splash")
        self.canvas.delete("overlay")
        self._fb_photo = None
        if self.core.is_running:
            self.canvas.create_text(
                160, 118,
                text="Rendering...",
                fill="#444444", font=UI_FONT, justify="center", tags="splash",
            )
            return
        blob = (
            bytes(self.core.rom[:8192])
            if len(self.core.rom) >= 16
            else bytes(self.core.rdram[:8192])
        )
        digest = hashlib.sha256(blob).digest()
        for gy in range(15):
            for gx in range(20):
                i = (gy * 20 + gx) % len(digest)
                v = digest[i]
                r = (v ^ (i * 13)) & 0xFF
                g = ((v << 1) ^ (gy * 31)) & 0xFF
                b = ((v << 2) ^ (gx * 17)) & 0xFF
                col = f"#{r:02x}{g:02x}{b:02x}"
                self.canvas.create_rectangle(
                    gx * 16, gy * 16,
                    gx * 16 + 16, gy * 16 + 16,
                    fill=col, outline="#101010", width=0, tags="fb",
                )
        lines = ["cathle static preview", "from ROM / RDRAM digest"]
        if subtitle:
            lines.append(subtitle)
        self.canvas.create_text(
            160, 118,
            text="\n".join(lines),
            fill="#e8e8e8", font=UI_FONT, justify="center", tags="splash",
        )

    def _refresh_vi_framebuffer(self) -> None:
        if self.canvas is None:
            return
        if not self.core._vi_origin_set:
            if self.core.is_running:
                self.canvas.delete("fb")
                self.canvas.delete("splash")
                self.canvas.delete("overlay")
                self._fb_photo = None
                self.canvas.create_text(
                    160, 118, text=f"Booting...\nPC: 0x{self.core.cpu.pc:08X}",
                    fill="#888888", font=UI_FONT, justify="center", tags="splash",
                )
            else:
                self._canvas_static_fallback("Waiting for VI init")
            return
        ppm = self.core.vi_framebuffer_ppm()
        if not ppm:
            if self.core.is_running:
                self.canvas.delete("fb")
                self.canvas.delete("splash")
                self.canvas.delete("overlay")
                self._fb_photo = None
            else:
                self._canvas_static_fallback("No RGB5551 tile at common VI origins")
            return
        photo = None
        master = self.canvas.winfo_toplevel()
        try:
            b64 = base64.b64encode(ppm).decode("ascii")
            photo = tk.PhotoImage(master=master, data=b64, format="ppm")
        except tk.TclError:
            try:
                from PIL import Image, ImageTk
                photo = ImageTk.PhotoImage(Image.open(io.BytesIO(ppm)), master=master)
            except Exception:
                photo = None
        if photo is None:
            if self.core.is_running:
                return
            self._canvas_static_fallback("Framebuffer PPM decode failed")
            return
        self._fb_photo = photo
        self.canvas.delete("fb")
        self.canvas.delete("splash")
        self.canvas.create_image(0, 0, anchor="nw", image=self._fb_photo, tags="fb")

    def _update_loop(self) -> None:
        if self.core.is_running and self.canvas is not None:
            self.core.run_frame()

            self._refresh_vi_framebuffer()
            self.canvas.delete("overlay")
            for cmd in self.core.rdp_draw_commands:
                self.canvas.create_rectangle(
                    cmd["x"], cmd["y"], cmd["x"] + 10, cmd["y"] + 10,
                    fill=cmd["color"], tags="overlay",
                )

            mon_text = (
                f"{APP_NAME}  [{ENGINE_NAME}]\n"
                f"{'=' * 36}\n"
                f"State  : RUNNING\n"
                f"Frame  : {self.core.frame_count}\n\n"
                f"R4300 PC : 0x{self.core.cpu.pc:08X}\n"
                f"TLB      : 32 entries\n"
                f"HLE cnt  : {self.core.hle_calls}\n\n"
                f"VI origin (reg) : 0x{(self.core.bus.regs.get(VI_ORIGIN_REG, 0) & 0xFFFFFF):06X}\n"
                f"VI preview phys : 0x{self.core.vi_framebuffer_phys_origin():06X}\n"
                f"RDP cmds        : {len(self.core.rdp_draw_commands)}\n"
                f"Controller      : 0x{self.core.controller_state:04X}\n"
                f"AI bytes        : {self.core.audio_samples_played}\n"
            )
            self._set_monitor_text(mon_text)

        self.root.after(16, self._update_loop)

    def run(self):
        self.root.mainloop()

def main() -> None:
    if tk is None or ttk is None:
        print("Fatal: Tkinter (and ttk) are required for this GUI.")
        sys.exit(1)
    try:
        app = ACsN64GUI()
    except RuntimeError as exc:
        print(f"Fatal: {exc}")
        sys.exit(1)
    if not getattr(app, "root", None):
        print("Fatal: GUI failed to initialize.")
        sys.exit(1)
    app.run()

if __name__ == "__main__":
    main()
