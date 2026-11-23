# build_overlay.tcl - wrap one Vitis HLS IP into a KV260 block design and produce <name>.bit + <name>.hwh for PYNQ.
#
#   vivado -mode batch -nojournal -nolog -source build_overlay.tcl -tclargs <hls-ip-dir> <name> [pl-clock-MHz] [jobs]
#
# <hls-ip-dir>: the folder holding the exported IP (component.xml), e.g. <smoke work dir>/smoke/solution1/impl/ip
# The HLS IP must have an s_axilite control port and m_axi master ports with offset=slave (the Rodinia kernels do).
# Optional: BOARD_REPO=<path to XilinxBoardStore checkout> to use the KV260 board preset for the PS.
# Output in the current folder: <name>.bit, <name>.hwh, <name>_timing.rpt, <name>_util.rpt

set ip_repo [file normalize [lindex $argv 0]]
set name    [lindex $argv 1]
set freq    [expr {[llength $argv] > 2 ? [lindex $argv 2] : 100}]
set jobs    [expr {[llength $argv] > 3 ? [lindex $argv 3] : 4}]
set part    xck26-sfvc784-2LV-c
set pdir    [file normalize ./ov_$name]

if {[info exists ::env(BOARD_REPO)]} { set_param board.repoPaths $::env(BOARD_REPO) }
create_project -force ov_$name $pdir -part $part
set bp [lindex [lsort [get_board_parts -quiet *kv260_som*]] end]
if {$bp ne ""} {
  set_property board_part $bp [current_project]
  puts "INFO: board part $bp"
} else {
  puts "WARNING: KV260 board files not found; the PS uses default settings (fine for PYNQ, which only loads the PL)"
}

set_property ip_repo_paths $ip_repo [current_project]
update_ip_catalog
set hls [get_ipdefs -filter {VLNV =~ "*:hls:*"}]
if {[llength $hls] != 1} { puts "ERROR: expected exactly one HLS IP in $ip_repo, found: $hls"; exit 1 }

create_bd_design design_1
set ps [create_bd_cell -type ip -vlnv [lindex [get_ipdefs -filter {NAME == zynq_ultra_ps_e}] end] zynq_ultra_ps_e_0]
if {$bp ne ""} { apply_bd_automation -rule xilinx.com:bd_rule:zynq_ultra_ps_e -config {apply_board_preset "1"} $ps }
# One PS master (HPM0_FPD) for the control registers, one PS slave (HP0_FPD) for the kernel's memory masters,
# PL clock 0 at the requested frequency
set_property -dict [list \
  CONFIG.PSU__USE__M_AXI_GP0 {1} CONFIG.PSU__USE__M_AXI_GP1 {0} CONFIG.PSU__USE__M_AXI_GP2 {0} \
  CONFIG.PSU__USE__S_AXI_GP2 {1} \
  CONFIG.PSU__FPGA_PL0_ENABLE {1} CONFIG.PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ $freq] $ps

if {[get_bd_intf_pins -quiet zynq_ultra_ps_e_0/S_AXI_HP0_FPD] eq ""} {
  puts "ERROR: S_AXI_HP0_FPD is not enabled on the PS. Its AXI ports are: [get_bd_intf_pins -of_objects $ps]"
  exit 1
}
set k [create_bd_cell -type ip -vlnv $hls kernel_0]
apply_bd_automation -rule xilinx.com:bd_rule:axi4 \
  -config {Master /zynq_ultra_ps_e_0/M_AXI_HPM0_FPD Clk_master {Auto} Clk_slave {Auto} Clk_xbar {Auto} intc_ip {New AXI Interconnect} master_apb {0}} \
  [get_bd_intf_pins kernel_0/s_axi_control]
# Memory path: each kernel m_axi master -> PS S_AXI_HP0_FPD.
# First try the automation in the form Vivado records in its journal (run on the PS slave pin);
# if that fails, wire it by hand through a SmartConnect.
set masters [get_bd_intf_pins -of_objects $k -filter {MODE == Master && VLNV =~ "*aximm*"}]
set auto_ok 1
foreach m $masters {
  if {[catch {apply_bd_automation -rule xilinx.com:bd_rule:axi4 \
        -config [list Clk_master {Auto} Clk_slave {Auto} Clk_xbar {Auto} Master $m \
                      Slave {/zynq_ultra_ps_e_0/S_AXI_HP0_FPD} ddr_seg {Auto} intc_ip {New AXI SmartConnect} master_apb {0}] \
        [get_bd_intf_pins zynq_ultra_ps_e_0/S_AXI_HP0_FPD]} err]} {
    puts "WARNING: automation for $m failed ($err); wiring by hand"
    set auto_ok 0
    break
  }
}
if {!$auto_ok} {
  set sc [create_bd_cell -type ip -vlnv [lindex [get_ipdefs -filter {NAME == smartconnect}] end] smc_mem]
  set_property CONFIG.NUM_SI [llength $masters] $sc
  set i 0
  foreach m $masters {
    connect_bd_intf_net $m [get_bd_intf_pins $sc/S[format %02d $i]_AXI]
    incr i
  }
  connect_bd_intf_net [get_bd_intf_pins $sc/M00_AXI] [get_bd_intf_pins zynq_ultra_ps_e_0/S_AXI_HP0_FPD]
  set clk [get_bd_pins zynq_ultra_ps_e_0/pl_clk0]
  connect_bd_net $clk [get_bd_pins $sc/aclk]
  connect_bd_net $clk [get_bd_pins zynq_ultra_ps_e_0/saxihp0_fpd_aclk]
  set rst [lindex [get_bd_cells -filter {VLNV =~ "*proc_sys_reset*"}] 0]
  connect_bd_net [get_bd_pins $rst/peripheral_aresetn] [get_bd_pins $sc/aresetn]
}
assign_bd_address
validate_bd_design
save_bd_design

add_files -norecurse [make_wrapper -files [get_files design_1.bd] -top]
set_property top design_1_wrapper [current_fileset]
update_compile_order -fileset sources_1
launch_runs impl_1 -to_step write_bitstream -jobs $jobs
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} { puts "ERROR: implementation did not finish; see $pdir"; exit 1 }

file copy -force [lindex [glob $pdir/ov_$name.runs/impl_1/*.bit] 0] ./$name.bit
set hwh [glob -nocomplain $pdir/ov_$name.gen/sources_1/bd/design_1/hw_handoff/design_1.hwh \
                           $pdir/ov_$name.srcs/sources_1/bd/design_1/hw_handoff/design_1.hwh]
file copy -force [lindex $hwh 0] ./$name.hwh
open_run impl_1
report_timing_summary -file ./${name}_timing.rpt
report_utilization    -file ./${name}_util.rpt
puts "DONE $name: WNS=[get_property SLACK [get_timing_paths -max_paths 1 -setup]] ns at $freq MHz -> $name.bit $name.hwh"
