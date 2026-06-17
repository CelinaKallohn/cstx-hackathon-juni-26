import { Component, ElementRef, ViewChild, AfterViewInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import * as echarts from 'echarts/core';
import { BarChart } from 'echarts/charts';
import { TitleComponent, TooltipComponent, GridComponent, LegendComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { Subscription } from 'rxjs';
import { DateSelectionService } from '../services/date-selection.service';

// Register required ECharts components for bar chart
echarts.use([BarChart, TitleComponent, TooltipComponent, GridComponent, LegendComponent, CanvasRenderer]);

@Component({
  selector: 'app-lastgang',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './lastgang.component.html',
  styleUrls: ['./lastgang.component.css'],
})
export class Lastgang implements AfterViewInit, OnDestroy {
  @ViewChild('chart', { static: true }) chartEl!: ElementRef<HTMLDivElement>;
  private chartInstance: echarts.ECharts | null = null;

  // parsed data: date (DD.MM.YYYY) -> { times: string[], loads: number[] }
  private dataByDate: Record<string, { times: string[]; loads: number[] }> = {};
  dates: string[] = [];
  selectedDateIndex = 0;

  private dateSub?: Subscription;

  constructor(private readonly dateService: DateSelectionService) {}

  ngAfterViewInit(): void {
    this.chartInstance = echarts.init(this.chartEl.nativeElement);
    this.init().catch(err => console.error('Fehler beim Initialisieren Lastgang:', err));
  }

  private async init(): Promise<void> {
    try {
      const resp = await fetch('/collected_cleaned_data.csv');
      if (!resp.ok) throw new Error('Konnte CSV nicht laden');
      const txt = await resp.text();
      this.parseCsv(txt);

      const initDate = this.selectedDate();
      if (initDate) {
        this.updateChartForDate(initDate);
      }

      // Merge available dates from both components and publish to service
      const currentDates = (this.dateService as any)['datesSubject'].getValue() || [];
      const mergedDates = Array.from(new Set([...currentDates, ...this.dates])).sort((a, b) => {
        const pa = a.split('.').map(Number);
        const pb = b.split('.').map(Number);
        const da = new Date(pa[2], pa[1] - 1, pa[0]);
        const db = new Date(pb[2], pb[1] - 1, pb[0]);
        return da.getTime() - db.getTime();
      });
      if (mergedDates.length > 0) {
        this.dateService.setAvailableDates(mergedDates);
      }

      // subscribe for date changes from the global DateSelectionService
      this.dateSub = this.dateService.date$.subscribe(iso => {
        if (!iso) return;
        const parts = iso.split('-');
        if (parts.length !== 3) return;
        const dateStr = `${parts[2].padStart(2, '0')}.${parts[1].padStart(2, '0')}.${parts[0]}`;
        const idx = this.dates.indexOf(dateStr);
        if (idx !== -1 && idx !== this.selectedDateIndex) {
          this.selectedDateIndex = idx;
          if (dateStr) {
            this.updateChartForDate(dateStr);
          }
        }
      });

      window.addEventListener('resize', this.onResize);
    } catch (err) {
      console.error(err);
    }
  }

  public selectedDate(): string | null {
    return this.dates.length > 0 ? this.dates[this.selectedDateIndex] : null;
  }

  private parseCsv(content: string) {
    const lines = content.split(/\r?\n/).map(l => l.trim()).filter(l => l.length > 0);
    if (lines.length === 0) return;

    // header: Datum;Uhrzeit;"Profilwert kWh";"Profilwert kW";...
    const header = lines[0].split(';').map(h => h.toLowerCase().replaceAll('"', ''));
    const dateIdx = Math.max(0, header.findIndex(h => h.includes('datum')));
    const timeIdx = Math.max(1, header.findIndex(h => h.includes('uhrzeit')));
    const loadIdx = Math.max(2, header.findIndex(h => h.includes('profilwert') && h.includes('kwh')));

    for (let i = 1; i < lines.length; i++) {
      const cols = lines[i].split(';').map(c => c.replaceAll('"', ''));
      if (cols.length <= Math.max(dateIdx, timeIdx, loadIdx)) continue;

      // Normalize date: "1.1.2025" -> "01.01.2025"
      let dateStr = cols[dateIdx];
      const dateParts = dateStr.split('.');
      if (dateParts.length !== 3) continue;
      dateStr = `${dateParts[0].padStart(2, '0')}.${dateParts[1].padStart(2, '0')}.${dateParts[2]}`;

      const time = cols[timeIdx]; // HH:MM:SS
      let loadStr = cols[loadIdx] || '';
      loadStr = loadStr.replaceAll('.', '').replace(',', '.');
      const load = Number.parseFloat(loadStr);
      if (Number.isNaN(load)) continue;

      if (!this.dataByDate[dateStr]) this.dataByDate[dateStr] = { times: [], loads: [] };
      this.dataByDate[dateStr].times.push(time);
      this.dataByDate[dateStr].loads.push(load);
    }

    this.dates = Object.keys(this.dataByDate).sort((a, b) => {
      const pa = a.split('.').map(Number);
      const pb = b.split('.').map(Number);
      const da = new Date(pa[2], pa[1] - 1, pa[0]);
      const db = new Date(pb[2], pb[1] - 1, pb[0]);
      return da.getTime() - db.getTime();
    });
    this.selectedDateIndex = Math.max(0, this.dates.length - 1);
  }

  private updateChartForDate(date: string) {
    const d = this.dataByDate[date];
    if (!d) return;
    const option: any = {
      title: { text: `Lastgang — ${date}` },
      tooltip: { trigger: 'axis' },
      xAxis: {
        type: 'category',
        data: d.times,
        boundaryGap: false,
        name: 'Uhrzeit',
        axisLabel: {
          interval: 3, // Show every 4th element (for 15-min intervals -> hourly display)
          formatter: (value: string) => {
            // Remove seconds if present (HH:MM:SS -> HH:MM)
            const parts = value.split(':');
            return parts.length >= 2 ? `${parts[0]}:${parts[1]}` : value;
          },
        },
      },
      yAxis: { type: 'value', name: 'Last (kWh)' },
      series: [
        {
          name: 'Last (kWh)',
          type: 'bar',
          data: d.loads,
          itemStyle: { opacity: 0.9 },
        },
      ],
      grid: { left: '10%', right: '10%', bottom: '15%' },
    };
    this.chartInstance?.setOption(option);
  }

  // navigation is handled globally via DateSelectionService / Datepicker

  private readonly onResize = () => {
    if (this.chartInstance) this.chartInstance.resize();
  };

  ngOnDestroy(): void {
    window.removeEventListener('resize', this.onResize);
    if (this.chartInstance) {
      this.chartInstance.dispose();
      this.chartInstance = null;
    }
    if (this.dateSub) this.dateSub.unsubscribe();
  }
}



