import { Component, ElementRef, ViewChild, AfterViewInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import * as echarts from 'echarts/core';
import { BarChart } from 'echarts/charts';
import { TitleComponent, TooltipComponent, GridComponent, LegendComponent, MarkLineComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { Subscription } from 'rxjs';
import { DateSelectionService } from '../services/date-selection.service';
import { CsvDataService } from '../services/csv-data.service';
// Register required ECharts components for stacked bar chart with markline
echarts.use([BarChart, TitleComponent, TooltipComponent, GridComponent, LegendComponent, MarkLineComponent, CanvasRenderer]);

interface DayData {
  times: string[];
  taxesAndCharges: number[]; // ct/kWh -> € conversion
  workPrice: number[]; // ct/kWh -> € conversion
  spotPrice: number[]; // ct/kWh -> € conversion
}

@Component({
  selector: 'app-ausgaben',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './ausgaben.component.html',
  styleUrls: ['./ausgaben.component.css'],
})
export class Ausgaben implements AfterViewInit, OnDestroy {
  @ViewChild('chart', { static: true }) chartEl!: ElementRef<HTMLDivElement>;
  private chartInstance: echarts.ECharts | null = null;

  dates: string[] = [];
  selectedDateIndex = 0;

  private dateSub?: Subscription;
  private readonly referencePrice = 59; // ct (customer price)

  constructor(private readonly dateService: DateSelectionService, private readonly csvService: CsvDataService) {}

  ngAfterViewInit(): void {
    this.chartInstance = echarts.init(this.chartEl.nativeElement);
    this.init().catch(err => console.error('Fehler beim Initialisieren Ausgaben:', err));
  }

  private async init(): Promise<void> {
    try {
      await this.csvService.ensureLoaded();
      this.dates = this.csvService.getAvailableDates();
      this.selectedDateIndex = Math.max(0, this.dates.length - 1);

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
           this.updateChartForDate(dateStr);
         } else if (idx === -1 && !this.dates.includes(dateStr)) {
           // Date not available, show empty chart
           this.updateChartForDate(dateStr);
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

   private updateChartForDate(date: string) {
     const d = this.csvService.getAusgabenDataByDate(date);
     if (!d || d.times.length === 0) {
        // Show empty chart if no data available
        const emptyOption: any = {
          title: { text: `Ausgaben`, textStyle: { color: '#000' } },
          legend: { data: ['Steuern & Arbeitspreis', 'Spotpreis'], top: '4%', left: 'center', textStyle: { color: '#000' } },
          tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
          xAxis: { type: 'category', data: [], axisLabel: { color: '#000' } },
          yAxis: { type: 'value', name: 'Ausgaben (ct)', min: 0, axisLabel: { color: '#000' } },
          series: [],
          grid: { left: '10%', right: '10%', top: '14%', bottom: '15%' },
        };
        this.chartInstance?.setOption(emptyOption, true);
        return;
     }

    const maxValues = d.taxesAndCharges.map((tax, i) => tax + d.spotPrice[i]);
    const dayMax = maxValues.length ? Math.max(...maxValues) : 0;
    const yAxisMax = Math.max(70, dayMax * 1.1);

     const option: any = {
       title: { text: `Ausgaben`, textStyle: { color: '#000' } },
       // show legend for the stacked bars (colors)
       legend: { data: ['Steuern & Arbeitspreis', 'Spotpreis'], top: '4%', left: 'center', textStyle: { color: '#000' } },
       tooltip: {
         trigger: 'axis',
         axisPointer: { type: 'shadow' },
         formatter: (params: any) => {
           if (!Array.isArray(params)) params = [params];
           let result = params[0].axisValue + '<br/>';
           for (const p of params) {
             if (p.seriesName === 'Steuern & Arbeitspreis') {
               result += `${p.seriesName}: ${p.value.toFixed(2)}ct<br/>`;
             } else if (p.seriesName === 'Spotpreis') {
               result += `${p.seriesName}: ${p.value.toFixed(2)}ct<br/>`;
             }
           }
           return result;
         },
       },
       xAxis: {
         type: 'category',
         data: d.times,
         boundaryGap: true,
         // axis name removed: keep tick labels but no axis title
         axisLabel: {
           interval: 7,
           color: '#000',
           formatter: (value: string) => {
             const parts = value.split(':');
             return parts.length >= 2 ? `${parts[0]}:${parts[1]}` : value;
           },
         },
       },
       yAxis: { type: 'value', name: 'Ausgaben (ct)', min: 0, max: yAxisMax, axisLabel: { color: '#000' } },
       series: [
         {
           name: 'Steuern & Arbeitspreis',
           type: 'bar',
           data: d.taxesAndCharges,
           stack: 'total',
           itemStyle: { color: '#5470C6', opacity: 0.5 },
         },
         {
           name: 'Spotpreis',
           type: 'bar',
           data: d.spotPrice,
           stack: 'total',
           itemStyle: { color: '#ff9100' },
           markLine: {
             data: [
               {
                 name: 'Kundenpreis (59ct)',
                 yAxis: this.referencePrice,
                 lineStyle: { color: '#000', type: 'solid', width: 2 },
                 label: { position: 'insideEndTop', offset: [-10, -5] },
               },
             ],
           },
         },
       ],
       // leave space at the top for the legend
       grid: { left: '10%', right: '10%', top: '14%', bottom: '15%' },
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

