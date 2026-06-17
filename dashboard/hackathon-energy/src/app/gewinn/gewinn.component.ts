import { Component, ElementRef, ViewChild, AfterViewInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import * as echarts from 'echarts/core';
import { LineChart } from 'echarts/charts';
import { TitleComponent, TooltipComponent, GridComponent, LegendComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { Subscription } from 'rxjs';
import { DateSelectionService } from '../services/date-selection.service';

// Register required ECharts components for line chart
echarts.use([LineChart, TitleComponent, TooltipComponent, GridComponent, LegendComponent, CanvasRenderer]);

interface DayData {
  times: string[];
  profit: number[]; // in €
}

@Component({
  selector: 'app-gewinn',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './gewinn.component.html',
  styleUrls: ['./gewinn.component.css'],
})
export class Gewinn implements AfterViewInit, OnDestroy {
  @ViewChild('chart', { static: true }) chartEl!: ElementRef<HTMLDivElement>;
  private chartInstance: echarts.ECharts | null = null;

  // parsed data: date (DD.MM.YYYY) -> { times, profit }
  private dataByDate: Record<string, DayData> = {};
  dates: string[] = [];
  selectedDateIndex = 0;

  private dateSub?: Subscription;

  constructor(private readonly dateService: DateSelectionService) {}

  ngAfterViewInit(): void {
    this.chartInstance = echarts.init(this.chartEl.nativeElement);
    this.init().catch(err => console.error('Fehler beim Initialisieren Gewinn:', err));
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

      // Merge available dates from other components
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

      // Subscribe for date changes
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

    // header: ...;Gewinn
    const header = lines[0].split(';').map(h => h.toLowerCase().replaceAll('"', ''));
    const dateIdx = Math.max(0, header.findIndex(h => h.includes('datum')));
    const timeIdx = Math.max(1, header.findIndex(h => h.includes('uhrzeit')));
    const gainIdx = Math.max(8, header.findIndex(h => h.includes('gewinn')));

    for (let i = 1; i < lines.length; i++) {
      const cols = lines[i].split(';').map(c => c.replaceAll('"', ''));
      if (cols.length <= Math.max(dateIdx, timeIdx, gainIdx)) continue;

      // Normalize date
      let dateStr = cols[dateIdx];
      const dateParts = dateStr.split('.');
      if (dateParts.length !== 3) continue;
      dateStr = `${dateParts[0].padStart(2, '0')}.${dateParts[1].padStart(2, '0')}.${dateParts[2]}`;

      const time = cols[timeIdx];

      // Parse gain value
      let gainStr = cols[gainIdx] || '';
      gainStr = gainStr.replaceAll('.', '').replace(',', '.');
      const gain = Number.parseFloat(gainStr);

      if (Number.isNaN(gain)) continue;

      if (!this.dataByDate[dateStr]) {
        this.dataByDate[dateStr] = { times: [], profit: [] };
      }
      this.dataByDate[dateStr].times.push(time);
      this.dataByDate[dateStr].profit.push(gain / 100); // Convert from ct to €
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

    // Calculate min/max for dynamic y-axis
    const profitMin = d.profit.length ? Math.min(...d.profit) : 0;
    const profitMax = d.profit.length ? Math.max(...d.profit) : 0;
    const padding = (profitMax - profitMin) * 0.1 || 5;
    const yMin = Math.floor(profitMin - padding);
    const yMax = Math.ceil(profitMax + padding);

    // Split data into positive and negative for separate series with different colors
    const positiveData = d.profit.map(v => v >= 0 ? v : undefined);
    const negativeData = d.profit.map(v => v < 0 ? v : undefined);

    const option: any = {
      title: { text: `Gewinn` },
      tooltip: {
        trigger: 'axis',
        formatter: (params: any) => {
          if (!Array.isArray(params)) params = [params];
          let result = params[0].axisValue + '<br/>';
          for (const p of params) {
            const value = p.value;
            if (value !== undefined && value !== null) {
              const label = value >= 0 ? `✓ Gewinn: €${Math.abs(value).toFixed(2)}` : `✗ Verlust: €${Math.abs(value).toFixed(2)}`;
              result += label + '<br/>';
            }
          }
          return result;
        },
      },
      xAxis: {
        type: 'category',
        data: d.times,
        boundaryGap: false,
        name: 'Uhrzeit',
        axisLabel: {
          interval: 7,
          formatter: (value: string) => {
            const parts = value.split(':');
            return parts.length >= 2 ? `${parts[0]}:${parts[1]}` : value;
          },
        },
      },
      yAxis: { type: 'value', name: 'Gewinn (€)', min: yMin, max: yMax },
      series: [
        {
          name: 'Gewinn',
          type: 'line',
          data: positiveData,
          smooth: true,
          itemStyle: { color: '#91CB74' },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: 'rgba(145, 203, 116, 0.3)' },
              { offset: 1, color: 'rgba(145, 203, 116, 0.1)' },
            ]),
          },
        },
        {
          name: 'Verlust',
          type: 'line',
          data: negativeData,
          smooth: true,
          itemStyle: { color: '#EE6666' },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: 'rgba(238, 102, 102, 0.3)' },
              { offset: 1, color: 'rgba(238, 102, 102, 0.1)' },
            ]),
          },
        },
      ],
      grid: { left: '10%', right: '10%', bottom: '15%' },
    };
    this.chartInstance?.setOption(option);
  }

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

